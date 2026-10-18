"""OpenAI Chat Completions requests as real clients send them, for the SpecPrefill route.

`parse` accepts the shapes agents and SDKs use (content-part lists, the
`developer` role, extra message keys, sampling fields, `stop`,
`stream_options`) and returns one normalized request. Anything the lean engine
cannot serve is listed in `native_reasons`, so the service answers it with
native Flash-Next instead of failing. Malformed requests raise ValueError.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

TEXT_PARTS = ('text', 'input_text', 'output_text')
SAMPLING = ('temperature', 'top_p', 'top_k', 'min_p', 'seed', 'presence_penalty', 'frequency_penalty',
            'repetition_penalty')
MAX_STOP = 4
# Fields the lean engine can't honor: their requests go to native Flash-Next.
NATIVE_FIELDS = ('logprobs', 'top_logprobs', 'logit_bias', 'functions', 'function_call', 'audio', 'modalities',
                 'prediction', 'web_search_options')


@dataclass
class ChatRequest:
    messages: list
    tools: list | None
    max_tokens: int | None
    sampling: dict
    stop: list
    stream: bool
    include_usage: bool
    native_reasons: list = field(default_factory=list)


def _text(content, where, reasons):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError(f'{where} must be a string or a list of content parts')
    texts = []
    for part in content:
        if not isinstance(part, dict) or not isinstance(part.get('type'), str):
            raise ValueError(f'{where} parts must be objects with a type')
        if part['type'] in TEXT_PARTS:
            if not isinstance(part.get('text'), str):
                raise ValueError(f'{where} text parts need a text string')
            texts.append(part['text'])
        else:
            reasons.append(f'content part {part["type"]}')
    return ''.join(texts)


def _arguments(value):
    if value is None:
        return '{}'
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _calls(calls, where):
    if not isinstance(calls, list):
        raise ValueError(f'{where}.tool_calls must be a list')
    out = []
    for i, call in enumerate(calls):
        function = call.get('function') if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not function['name']:
            raise ValueError(f'{where}.tool_calls[{i}] needs a named function')
        entry = {'type': 'function', 'function': {'name': function['name'],
                                                  'arguments': _arguments(function.get('arguments'))}}
        if isinstance(call.get('id'), str):
            entry['id'] = call['id']
        out.append(entry)
    return out


def _messages(raw, reasons):
    if not isinstance(raw, list) or not raw:
        raise ValueError('messages must be a nonempty list')
    out, started = [], False
    for i, m in enumerate(raw):
        where = f'messages[{i}]'
        if not isinstance(m, dict):
            raise ValueError(f'{where} must be an object')
        # Only the message being answered decides the route; an image earlier in
        # the history was answered then, and its text parts are kept.
        parts = reasons if i == len(raw) - 1 else []
        role = m.get('role')
        if role == 'developer':
            role = 'system'
        if role == 'function':
            reasons.append('legacy function message')
            role = 'tool'
        if role not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError(f'{where} has an unsupported role')
        message = {'role': role, 'content': _text(m.get('content'), where + '.content', parts)}
        if role == 'assistant' and m.get('tool_calls'):
            message['tool_calls'] = _calls(m['tool_calls'], where)
        if role == 'tool':
            if isinstance(m.get('tool_call_id'), str):
                message['tool_call_id'] = m['tool_call_id']
            if isinstance(m.get('name'), str):
                message['name'] = m['name']
        if role == 'system' and started:
            # A late system message is an instruction in the conversation (as Strata renders it).
            message = {'role': 'user', 'content': message['content']}
        started |= role != 'system'
        if role in ('system', 'user') and not message['content'].strip() and not parts:
            raise ValueError(f'{where} has no text')
        out.append(message)
    if out[-1]['role'] not in ('user', 'tool'):
        raise ValueError('A conversation must end with a user message or a tool result')
    return out


def _tools(tools, tool_choice, reasons):
    if tools is None or tools == []:
        tools = None
    elif not isinstance(tools, list):
        raise ValueError('tools must be a list')
    else:
        for tool in tools:
            function = tool.get('function') if isinstance(tool, dict) else None
            if not isinstance(tool, dict) or tool.get('type') != 'function':
                reasons.append('non-function tool')
                continue
            if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not function['name']:
                raise ValueError('Each tool needs type "function" and a named function')
    if tool_choice not in (None, 'auto'):
        if tool_choice == 'none' or tool_choice == 'required' or isinstance(tool_choice, dict):
            reasons.append('tool_choice ' + (tool_choice if isinstance(tool_choice, str) else 'named'))
        else:
            raise ValueError('tool_choice must be "auto", "none", "required", or a named function')
    return tools


def _limit(payload):
    value = payload.get('max_completion_tokens', payload.get('max_tokens'))
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError('max_tokens must be an integer')
    return value if value > 0 else None  # 0 or -1 (llama.cpp, OpenCode): no limit of its own.


def _sampling(payload):
    out = {}
    for key in SAMPLING:
        value = payload.get(key)
        if value is None:
            continue
        if key in ('top_k', 'seed'):
            if type(value) is not int:
                raise ValueError(f'{key} must be an integer')
        elif type(value) not in (int, float):
            raise ValueError(f'{key} must be a number')
        out[key] = value
    if out.get('temperature', 0) < 0 or not 0 < out.get('top_p', 1) <= 1:
        raise ValueError('temperature must be >= 0 and top_p in (0, 1]')
    return out


def _stop(payload):
    stop = payload.get('stop')
    if stop is None:
        return []
    stop = [stop] if isinstance(stop, str) else stop
    if not isinstance(stop, list) or not all(isinstance(s, str) for s in stop) or len(stop) > MAX_STOP:
        raise ValueError(f'stop must be a string or a list of at most {MAX_STOP} strings')
    return [s for s in stop if s]


def parse(payload, model):
    """Normalized request; ValueError for a malformed one."""
    if not isinstance(payload, dict):
        raise ValueError('The request body must be an object')
    if payload.get('model', model) != model:
        raise ValueError('Unknown model for the configured SpecPrefill gateway')
    if payload.get('n', 1) != 1:
        raise ValueError('Only n=1 is supported')
    if 'stream' in payload and type(payload['stream']) is not bool:
        raise ValueError('stream must be boolean')
    reasons = []
    messages = _messages(payload.get('messages'), reasons)
    tools = _tools(payload.get('tools'), payload.get('tool_choice'), reasons)
    reasons += [f'field {name}' for name in NATIVE_FIELDS if payload.get(name) not in (None, False, [], {})]
    response_format = payload.get('response_format')
    if response_format not in (None, {'type': 'text'}):
        reasons.append('response_format')
    options = payload.get('stream_options') or {}
    if not isinstance(options, dict):
        raise ValueError('stream_options must be an object')
    return ChatRequest(messages=messages, tools=tools, max_tokens=_limit(payload), sampling=_sampling(payload),
                       stop=_stop(payload), stream=bool(payload.get('stream', False)),
                       include_usage=bool(options.get('include_usage')), native_reasons=reasons)


def identity(message):
    """What a history entry is, for thread matching: role, text, and tool calls without their IDs."""
    entry = {'role': message['role'], 'content': (message.get('content') or '').strip()}
    calls = message.get('tool_calls')
    if calls:
        canonical = []
        for call in calls:
            arguments = call['function'].get('arguments', '{}')
            try:
                arguments = json.dumps(json.loads(arguments), sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                pass
            canonical.append([call['function']['name'], arguments])
        entry['tool_calls'] = canonical
    return entry
