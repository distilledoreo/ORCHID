"""OpenAI-shaped HTTP responses for the explicitly enabled native text route."""
import asyncio
import json
import time

from fastapi.responses import JSONResponse, StreamingResponse

from ..db import new_id
from ..ingestion import append_novel_messages
from .scheduling import CapacityError
from .service import RequestConflict, digest


def failure(error, *, request_error=False):
    if isinstance(error, RequestConflict):
        return 409, {'error': {'type': 'session_conflict', 'message': str(error)}}
    if isinstance(error, CapacityError):
        return 503, {'error': {'type': 'context_capacity_exceeded', 'message': str(error)}}
    if isinstance(error, ValueError) and request_error:
        return 400, {'error': {'type': 'invalid_request_error', 'message': str(error)}}
    return 503, {'error': {'type': 'specprefill_unavailable', 'message': 'Native SpecPrefill request failed; retry with the same request ID'}}


async def handle(request, payload, service, store):
    project = request.headers.get('x-memory-project')
    thread = request.headers.get('x-memory-thread')
    rid = request.headers.get('x-request-id', new_id('req'))
    try:
        if not project or not thread:
            raise ValueError('SpecPrefill requires x-memory-project and x-memory-thread headers')
        if max(len(project), len(thread), len(rid)) > 512:
            raise ValueError('Project, thread, and request IDs must be at most 512 characters')
        service.validate(payload)
    except ValueError as error:
        status, body = failure(error, request_error=True)
        return JSONResponse(body, status_code=status)

    def committed(result):
        storage_thread = 'specprefill-' + digest([project, thread])
        store.ensure_project_and_thread(project, storage_thread)
        append_novel_messages(store, project_id=project, thread_id=storage_thread,
            messages=payload['messages'], request_id=rid, source='request')
        append_novel_messages(store, project_id=project, thread_id=storage_thread,
            messages=[result['choices'][0]['message']], request_id=rid, source='response', usage=result['usage'])
        store.record_request_usage(storage_thread, rid, result['usage'])

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
            return JSONResponse(result, headers={'x-request-id': rid})
        except Exception as error:
            status, body = failure(error)
            return JSONResponse(body, status_code=status, headers={'x-request-id': rid})

    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        def text(value):
            loop.call_soon_threadsafe(queue.put_nowait, ('text', value))
        async def worker():
            try:
                result = await run(text)
                await queue.put(('done', result))
            except Exception as error:
                await queue.put(('error', failure(error)[1]))
        start(worker())
        base = {'id': 'chatcmpl-' + digest([digest([project, thread]), rid])[:24], 'object': 'chat.completion.chunk',
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
            elif kind == 'error':
                yield 'data: ' + json.dumps(value) + '\n\n'
                break
            else:
                calls = value['choices'][0]['message'].get('tool_calls')
                if calls:
                    yield chunk({'tool_calls': [{'index': i, **c} for i, c in enumerate(calls)]})
                yield chunk({}, value['choices'][0]['finish_reason'])
                break
        yield 'data: [DONE]\n\n'

    return StreamingResponse(events(), media_type='text/event-stream', headers={'x-request-id': rid})
