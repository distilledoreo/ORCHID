"""OpenAI Responses API (POST /v1/responses) on the SpecPrefill route, for Codex CLI.

Codex speaks only the Responses API. Strata's converter (serve/responses.py,
the code Strata's own server uses for Codex) turns the request into chat
messages and tools; the chat path answers it, lean or native fallback; and
Strata's Assembler writes the Responses output items and typed events. The
server is stateless: the client sends the whole conversation in `input`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi.responses import JSONResponse, StreamingResponse

from .http import committer, failure


def strata_modules(config_path):
    """(serve.responses, serve.frontend) from the Strata checkout the config names."""
    root = json.loads(Path(config_path).read_text())['cwd']
    if root not in sys.path:
        sys.path.insert(0, root)
    from serve import frontend, responses
    return responses, frontend


def _openai_content(content):
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part.get('type') == 'text':
            parts.append({'type': 'text', 'text': part.get('text', '')})
        elif part.get('type') == 'image':
            parts.append({'type': 'image_url', 'image_url': {'url': part.get('source')}})
    return parts


def to_chat(req, rs):
    """A Responses request as a Chat Completions payload, plus Strata's tool-name map."""
    rs.check_request(req)
    tools, names, _ = rs.request_tools(req)
    messages = []
    for m in rs.input_messages(req):
        out = {'role': m['role'], 'content': _openai_content(m.get('content') or '')}
        if m.get('tool_calls'):
            out['tool_calls'] = [{'type': 'function', 'function': {
                'name': c['function']['name'],
                'arguments': json.dumps(c['function'].get('arguments') or {}, ensure_ascii=False)}}
                for c in m['tool_calls']]
        messages.append(out)
    chat = {'messages': messages}
    if tools:
        chat['tools'] = [{'type': 'function', 'function': t} for t in tools]
    choice = req.get('tool_choice')
    if choice in ('required', 'none'):
        chat['tool_choice'] = choice
    elif isinstance(choice, dict) and choice.get('name'):
        chat['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
    for source, target in (('max_output_tokens', 'max_tokens'), ('temperature', 'temperature'),
                           ('top_p', 'top_p')):
        if req.get(source) is not None:
            chat[target] = req[source]
    text = req.get('text') if isinstance(req.get('text'), dict) else {}
    if (text.get('format') or {}).get('type') not in (None, 'text'):
        chat['response_format'] = {'type': 'json_object'}  # Structured output: served natively.
    return chat, names


def answer_events(asm, rs, fe, result):
    """Output items for a finished chat answer whose text was already fed as it streamed."""
    events = []
    message = result['choices'][0]['message']
    for call in message.get('tool_calls') or ():
        try:
            arguments = json.loads(call['function'].get('arguments') or '{}')
        except ValueError:
            arguments = {'arguments': call['function'].get('arguments')}
        if not isinstance(arguments, dict):
            arguments = {'arguments': arguments}
        tool = fe.ToolCall(call['function']['name'], arguments, call.get('id') or rs.new_id('call'))
        events += asm.feed(fe.Event('tool_call', call=tool))
    usage = result.get('usage') or {}
    events += asm.finish({'finish': 'length' if result['choices'][0]['finish_reason'] == 'length' else 'stop',
                          'completion_tokens': usage.get('completion_tokens', 0),
                          'prompt_tokens': usage.get('prompt_tokens', 0)})
    return events


async def handle(request, req, service, store):
    try:
        rs, fe = strata_modules(service.options.config_path)
    except (OSError, ValueError, KeyError, ImportError):
        return JSONResponse({'error': {'type': 'server_error',
                                       'message': 'The Responses API needs the Strata checkout named by the config'}},
                            status_code=503)
    try:
        chat, names = to_chat(req, rs)
        parsed = service.validate(chat)
    except ValueError as error:
        status, body = failure(error, request_error=True)
        return JSONResponse(body, status_code=status)
    rid = request.headers.get('x-request-id') or None
    committed = committer(store, None, None, rid, parsed, chat)
    asm = rs.Assembler(req, service.options.model, 0, names, encrypted=False)

    async def run(on_text=None):
        return await asyncio.to_thread(service.complete, None, None, rid, chat, on_text, committed)

    if not req.get('stream'):
        try:
            result = await run()
        except Exception as error:
            status, body = failure(error, request_error=isinstance(error, ValueError))
            return JSONResponse(body, status_code=status)
        events = asm.start()
        text = result['choices'][0]['message'].get('content')
        if text:
            events += asm.feed(fe.Event('content', text))
        return JSONResponse(rs.collect(events + answer_events(asm, rs, fe, result)))

    async def stream():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def text(value):
            loop.call_soon_threadsafe(queue.put_nowait, ('text', value))

        async def worker():
            try:
                await queue.put(('done', await run(text)))
            except Exception as error:
                await queue.put(('error', error))
        task = asyncio.create_task(worker())
        request.app.state.specprefill_tasks.add(task)
        task.add_done_callback(request.app.state.specprefill_tasks.discard)

        def sse(events):
            return ''.join(f'event: {e["type"]}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n' for e in events)
        yield sse(asm.start())
        while True:
            kind, value = await queue.get()
            if kind == 'text':
                yield sse(asm.feed(fe.Event('content', value)))
            elif kind == 'error':
                yield sse([asm.failed(failure(value)[1]['error']['message'])])
                break
            else:
                yield sse(answer_events(asm, rs, fe, value))
                break

    return StreamingResponse(stream(), media_type='text/event-stream')
