"""OpenAI tool calling on top of the Qwen chat format, for the lean SpecPrefill route.

Tool definitions are rendered into the pinned system text the way the Qwen
chat template does. The model answers with ``<tool_call>{json}</tool_call>``
blocks, which are parsed back into OpenAI ``tool_calls``. Tool results and the
assistant's earlier calls are text the session ingests like any other source,
so a large tool output is read by the student and only its relevant excerpts
reach the target.
"""
from __future__ import annotations

import hashlib
import json
import re

_CALL = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.S)


def system_text(system, tools):
    """Append Qwen-format tool definitions to the system text."""
    if not tools:
        return system
    lines = '\n'.join(json.dumps(t, ensure_ascii=False, sort_keys=True) for t in tools)
    return (f'{system}\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n'
            'You are provided with function signatures within <tools></tools> XML tags:\n'
            f'<tools>\n{lines}\n</tools>\n\n'
            'For each function call, return a json object with function name and arguments within '
            '<tool_call></tool_call> XML tags:\n<tool_call>\n'
            '{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>')


def validate_tools(tools, tool_choice):
    if not isinstance(tools, list) or not tools:
        raise ValueError('tools must be a nonempty list')
    for tool in tools:
        function = tool.get('function') if isinstance(tool, dict) else None
        if (tool.get('type') != 'function' or not isinstance(function, dict)
                or not isinstance(function.get('name'), str) or not function['name']):
            raise ValueError('Each tool needs type "function" and a named function')
    if tool_choice not in (None, 'auto'):
        raise ValueError('Only tool_choice "auto" is supported')


def message_text(message):
    """Text a session ingests for a non-final history message."""
    role = message['role']
    if role == 'tool':
        return f'<tool_response>\n{message["content"]}\n</tool_response>'
    text = message.get('content') or ''
    for call in message.get('tool_calls') or ():
        function = call['function']
        arguments = function.get('arguments', '{}')
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            pass
        text += ('\n' if text else '') + '<tool_call>\n' + json.dumps(
            {'name': function['name'], 'arguments': arguments}, ensure_ascii=False) + '\n</tool_call>'
    return text


def parse(text, seed):
    """Split model output into (content or None, OpenAI tool_calls)."""
    calls = []
    for i, block in enumerate(_CALL.findall(text)):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get('name'), str):
            continue
        arguments = data.get('arguments', {})
        calls.append({'id': 'call_' + hashlib.sha256(f'{seed}:{i}'.encode()).hexdigest()[:24],
                      'type': 'function',
                      'function': {'name': data['name'],
                                   'arguments': arguments if isinstance(arguments, str)
                                   else json.dumps(arguments, ensure_ascii=False)}})
    if not calls:
        return text, []
    content = _CALL.sub('', text).strip()
    return content or None, calls
