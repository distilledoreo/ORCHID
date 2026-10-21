"""OpenAI tool calling in Flash-Next's own chat format, for the lean SpecPrefill route.

Tool definitions are rendered into the pinned system text exactly as the
model's chat template does (Strata serve/chat_template.jinja). The model
answers with ``<tool_call><function=NAME><parameter=P>...`` blocks (the
template's XML form; a JSON body is read too), which are parsed back into
OpenAI ``tool_calls`` the way Strata parses them. Tool results and the
assistant's earlier calls are text the session ingests in the same rendering.
"""
from __future__ import annotations

import hashlib
import json
import re

_CALL = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.S)


# The tools section of Flash-Next's chat template (Strata serve/chat_template.jinja),
# word for word: the model is trained on this XML call format.
_INSTRUCTIONS = (
    '\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n<tool_call>\n'
    '<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n'
    '<parameter=example_parameter_2>\nThis is the value for the second parameter\nthat can span\nmultiple lines\n'
    '</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\nReminder:\n- Function calls MUST follow the specified '
    'format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags\n'
    '- Required parameters MUST be specified\n- You may provide optional reasoning for your function call in natural '
    'language BEFORE the function call, but NOT after\n- If there is no function call available, answer the question '
    'like normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>')


def functions(tools):
    """OpenAI tools -> the function objects the template renders."""
    return [t.get('function', t) if t.get('type') == 'function' else t for t in tools or ()]


def system_text(system, tools):
    """The system text with Flash-Next's tools section, as its chat template renders it."""
    if not tools:
        return system
    listed = ''.join('\n' + json.dumps(f, ensure_ascii=False) for f in functions(tools))
    text = f'# Tools\n\nYou have access to the following functions:\n\n<tools>{listed}\n</tools>{_INSTRUCTIONS}'
    return text + ('\n\n' + system if system else '')


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


def arguments_of(raw):
    """A call's arguments from a client's history as a mapping (malformed JSON kept as text)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {'arguments': raw}
    return value if isinstance(value, dict) else {'arguments': raw}


def call_text(name, arguments):
    """One call in the template's XML form."""
    body = ''.join(f'<parameter={k}>\n{v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}\n</parameter>\n'
                   for k, v in arguments_of(arguments).items())
    return f'<tool_call>\n<function={name}>\n{body}</function>\n</tool_call>'


def message_text(message):
    """Text a session ingests for a non-final history message (the template's rendering)."""
    role = message['role']
    if role == 'tool':
        return f'<tool_response>\n{message["content"]}\n</tool_response>'
    text = message.get('content') or ''
    for k, call in enumerate(message.get('tool_calls') or ()):
        function = call['function']
        joint = ('\n\n' if text.strip() else '') if k == 0 else '\n'
        text += joint + call_text(function['name'], function.get('arguments'))
    return text


_CALL = re.compile(r'<tool_call>\s*(.*?)\s*(?:</tool_call>|\Z)', re.S)


def _xml_call(body, schemas):
    """`<function=NAME><parameter=P>VALUE</parameter>...</function>` -> (name, arguments), as Strata parses it:
    a value is JSON-decoded unless the tool's schema declares it a string."""
    name = body[len('<function='):body.index('>')].strip()
    rest = body[body.index('>') + 1:]
    props = ((schemas.get(name) or {}).get('parameters') or {}).get('properties') or {}
    arguments = {}
    while '<parameter=' in rest:
        rest = rest[rest.index('<parameter=') + len('<parameter='):]
        if '>' not in rest:
            break
        key, rest = rest[:rest.index('>')], rest[rest.index('>') + 1:]
        end = rest.find('</parameter>')
        value, rest = (rest[:end], rest[end + len('</parameter>'):]) if end >= 0 else (rest.split('</function>')[0], '')
        value = value[1:] if value.startswith('\n') else value
        value = value[:-1] if value.endswith('\n') else value
        if (props.get(key) or {}).get('type') == 'string':
            arguments[key] = value
        else:
            try:
                arguments[key] = json.loads(value)
            except ValueError:
                arguments[key] = value
    return name, arguments


def parse(text, seed, tools=None):
    """Split model output into (content or None, OpenAI tool_calls).

    Calls are in the template's XML form; a JSON body ({"name", "arguments"}) is read
    too. A last call cut off before its closing tag still counts.
    """
    schemas = {f.get('name'): f for f in functions(tools)}
    calls = []
    for i, block in enumerate(_CALL.findall(text)):
        name = arguments = None
        if block.startswith('<function=') and '>' in block:
            name, arguments = _xml_call(block, schemas)
        else:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get('name'), str):
                name, arguments = data['name'], data.get('arguments', data.get('parameters', {}))
        if not name or (schemas and name not in schemas):
            continue
        calls.append({'id': 'call_' + hashlib.sha256(f'{seed}:{i}'.encode()).hexdigest()[:24],
                      'type': 'function',
                      'function': {'name': name,
                                   'arguments': arguments if isinstance(arguments, str)
                                   else json.dumps(arguments, ensure_ascii=False)}})
    if not calls:
        return text, []
    content = _CALL.sub('', text).strip()
    return content or None, calls
