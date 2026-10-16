# Structural noise prefilter ruleset v1

This is a benchmark-only, trace-independent ruleset. It is frozen before
semantic outcome evaluation. The filter drops an item only when its typed
protocol structure proves that it is mechanical orchestration with no task
payload. Ambiguous content is kept.

## Rules

### `explicit_mechanical_wait_call` — DROP

Drop a typed `function_call` or `custom_tool_call`, or a tool call nested in a
typed `assistant_message`, only when:

- the call name is exactly one of `wait`, `wait_agent`, `poll`, or `sleep`; and
- its arguments parse as an object; and
- every argument key is in the mechanical wait-control set:
  `cell_id`, `duration_ms`, `max_tokens`, `poll_interval_ms`, `timeout_ms`, or
  `yield_time_ms`.

The rule is structural: it uses the typed operation name and argument schema,
not wording in the trace. Calls named `exec`, `bash`, `read`, `edit`, `test`, or
diagnostic tools are not matches. A wait-named call with an unknown argument
is kept.

### `empty_typed_tool_output` — DROP

Drop a typed tool output (`function_call_output`, `custom_tool_call_output`, or
FreetoShop `tool_result`) only when its typed output/content value is exactly
`null`, the empty string, an empty list, or an empty object.

Any list containing a text/object part is nonempty and is kept, including a
status-looking wrapper. Any nonempty command result, diagnostic, stack trace,
compiler/test output, or file content is kept.

### `typed_wait_protocol_result` — DROP

Drop a typed tool output only when its `call_id` links to a preceding explicit
mechanical wait call and its output/content parses as an object with exactly
the keys `message` and `timed_out`, with string and boolean values
respectively. This recognizes a wait protocol result without treating generic
status wording as empty.

Plain-text output from a wait call, objects with additional keys, and unlinked
objects are kept.

## Explicit non-matches

The filter does not drop code, source inspection, compiler/test output,
diagnostics, stack traces, commands, file contents, implementation analysis,
user instructions, assistant reasoning/state summaries, or coordination
artifacts. It does not use text-only rules for `stale`, `obsolete`, `failed`,
`test`, `diagnostic`, or `running`. If the typed structure is missing or
ambiguous, the decision is KEEP.

## Freeze

The canonical ruleset hash is recorded in `RULESET_HASH.txt`. The hash covers
the canonical JSON in `RULESET.json` only, using sorted object keys and compact
separators.
