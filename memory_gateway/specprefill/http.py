"""OpenAI-shaped HTTP responses for the explicitly enabled native text route."""
import asyncio
import json
import time

from fastapi.responses import JSONResponse, StreamingResponse

from ..db import new_id
from ..ingestion import append_novel_messages
from .scheduling import CapacityError
from .service import RequestConflict, Warming, digest


def failure(error, *, request_error=False):
    if isinstance(error, Warming):
        return 503, {'error': {'type': 'warming', 'message': str(error)}}
    if isinstance(error, RequestConflict):
        return 409, {'error': {'type': 'session_conflict', 'message': str(error)}}
    if isinstance(error, CapacityError):
        return 503, {'error': {'type': 'context_capacity_exceeded', 'message': str(error)}}
    if isinstance(error, ValueError) and request_error:
        return 400, {'error': {'type': 'invalid_request_error', 'message': str(error)}}
    return 503, {'error': {'type': 'specprefill_unavailable', 'message': 'Native SpecPrefill request failed; retry with the same request ID'}}


def committer(store, project, thread, rid, parsed, payload):
    """Records a committed turn in the gateway's event store (recall, debugging)."""
    def committed(result):
        key = (result.get('orchid') or {}).get('thread') or digest([project, thread])
        scope, request_id = project or 'auto', rid or result['id']
        storage_thread = 'specprefill-' + key
        store.ensure_project_and_thread(scope, storage_thread)
        messages = getattr(parsed, 'messages', None) or payload['messages']
        append_novel_messages(store, project_id=scope, thread_id=storage_thread,
            messages=messages, request_id=request_id, source='request')
        reply = {k: v for k, v in result['choices'][0]['message'].items() if k in ('role', 'content', 'tool_calls')}
        append_novel_messages(store, project_id=scope, thread_id=storage_thread,
            messages=[{**reply, 'content': reply.get('content') or ''}], request_id=request_id,
            source='response', usage=result['usage'])
        store.record_request_usage(storage_thread, request_id, result['usage'])
    return committed


async def handle(request, payload, service, store):
    project = request.headers.get('x-memory-project')
    thread = request.headers.get('x-memory-thread')
    lean = service.options.lean
    # The lean engine finds the thread from the history; a retry without a request
    # ID is recognized by its content. The compacting engine needs explicit scope.
    rid = request.headers.get('x-request-id') or (None if lean else new_id('req'))
    try:
        if not lean and (not project or not thread):
            raise ValueError('SpecPrefill requires x-memory-project and x-memory-thread headers')
        if bool(project) != bool(thread):
            raise ValueError('Send both x-memory-project and x-memory-thread, or neither')
        if max(len(project or ''), len(thread or ''), len(rid or '')) > 512:
            raise ValueError('Project, thread, and request IDs must be at most 512 characters')
        parsed = service.validate(payload)
    except ValueError as error:
        status, body = failure(error, request_error=True)
        return JSONResponse(body, status_code=status)

    if getattr(service, 'warming', False):
        return JSONResponse({'error': {'type': 'warming', 'message': 'The models are loading; retry in a few seconds'}},
                            status_code=503, headers={'Retry-After': '15'})
    include_usage = bool(getattr(parsed, 'include_usage', False))
    committed = committer(store, project, thread, rid, parsed, payload)

    async def run(on_text=None):
        return await asyncio.to_thread(service.complete, project, thread, rid, payload, on_text, committed)

    def start(coro):
        task = asyncio.create_task(coro)
        request.app.state.specprefill_tasks.add(task)
        def finished(future):
            request.app.state.specprefill_tasks.discard(future)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(finished)
        return task

    if not payload.get('stream', False):
        try:
            result = await asyncio.shield(start(run()))
            return JSONResponse(result, headers={'x-request-id': rid or result['id']})
        except Exception as error:
            status, body = failure(error, request_error=isinstance(error, ValueError) and lean)
            headers = {'x-request-id': rid} if rid else {}
            if body['error']['type'] == 'warming':
                headers['Retry-After'] = '15'
            return JSONResponse(body, status_code=status, headers=headers or None)

    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        def text(value, kind='content'):
            loop.call_soon_threadsafe(queue.put_nowait, ('text' if kind == 'content' else kind, value))
        async def worker():
            try:
                result = await run(text)
                await queue.put(('done', result))
            except Exception as error:
                await queue.put(('error', failure(error, request_error=isinstance(error, ValueError) and lean)[1]))
        start(worker())
        base = {'id': 'chatcmpl-' + digest([project, thread, rid or digest(payload)])[:24],
                'object': 'chat.completion.chunk',
                'created': int(time.time()), 'model': service.options.model}
        def chunk(delta, finish=None):
            return 'data: ' + json.dumps({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n'
        yield chunk({'role': 'assistant'})
        # Disconnect detaches delivery; the serialized worker finishes/checkpoints
        # its turn. It is never cancelled while a native cache is being mutated.
        while True:
            kind, value = await queue.get()
            if kind == 'text':
                yield chunk({'content': value})
            elif kind == 'reasoning':
                yield chunk({'reasoning_content': value})
            elif kind == 'error':
                yield 'data: ' + json.dumps(value) + '\n\n'
                break
            else:
                calls = value['choices'][0]['message'].get('tool_calls')
                if calls:
                    yield chunk({'tool_calls': [{'index': i, **c} for i, c in enumerate(calls)]})
                yield chunk({}, value['choices'][0]['finish_reason'])
                if include_usage:
                    yield 'data: ' + json.dumps({**base, 'choices': [], 'usage': value['usage']}) + '\n\n'
                break
        yield 'data: [DONE]\n\n'

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'x-request-id': rid} if rid else None)
