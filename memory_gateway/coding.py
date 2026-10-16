"""Optional coding views: immutable source handles and evidence-based file ledger."""

from __future__ import annotations

import copy
import json
import re
import sqlite3

from .context import estimate_tokens
from .message_views import event_messages, source_text

TOOLS = {"read": "read", "read_file": "read", "edit": "edited", "edit_file": "edited",
         "write": "edited", "write_file": "edited", "create_file": "created"}


def initialize_views(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS coding_prompt_views(
        capsule_id TEXT PRIMARY KEY, mask_before_sequence INTEGER NOT NULL)""")


def freeze_view(connection, thread_id, capsule_id, policy):
    if not policy:
        return
    # This is a foreground view frozen atomically at promotion, not additional
    # source material for the compaction job or a claim about later events.
    turns = connection.execute("""SELECT sequence FROM events WHERE thread_id=? AND role='assistant'
                                 ORDER BY sequence DESC LIMIT ?""", (thread_id, policy["keep_turns"])).fetchall()
    cutoff = min((row["sequence"] for row in turns), default=0)
    connection.execute("INSERT OR IGNORE INTO coding_prompt_views VALUES (?, ?)", (capsule_id, cutoff))


def update_ledger(events, previous=None, pending=None):
    ledger = copy.deepcopy(previous or {})
    waiting = copy.deepcopy(pending or {})
    for event in events:
        row = {"content": event.content, "role": event.role, "event_type": event.event_type}
        for message in event_messages(row):
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = function.get("name")
                    action = TOOLS.get(name)
                    try:
                        args = json.loads(function.get("arguments", "{}"))
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(args, dict):
                        continue
                    path = args.get("path", args.get("file_path", args.get("filename")))
                    targets = [(path, action)] if action else []
                    if name == "apply_patch":
                        patch = args.get("patch", args.get("input", ""))
                        if isinstance(patch, str):
                            targets = [(p.strip(), "created" if operation == "Add" else "edited")
                                       for operation, p in re.findall(r"^\*\*\* (Add|Update) File: (.+)$", patch, re.MULTILINE)]
                    call_id = call.get("id")
                    if not isinstance(call_id, str):
                        continue
                    valid = [(p, a) for p, a in targets if isinstance(p, str) and p and len(p) <= 512]
                    for path, action in valid:
                        entry = ledger.setdefault(path, {"read": [], "edited": [], "created": [], "attempted": []})
                        entry["attempted"].append({"action": action, "event_id": event.id})
                    if valid:
                        waiting[call_id] = {"targets": valid, "name": name, "event_id": event.id}
            elif message.get("role") == "tool":
                call = waiting.pop(message.get("tool_call_id"), None)
                if call is None:
                    continue
                content = message.get("content")
                try:
                    result = json.loads(content) if isinstance(content, str) else content
                except json.JSONDecodeError:
                    result = None
                failed = (isinstance(result, dict) and
                          (result.get("error") or result.get("isError") or result.get("status") in {"error", "failed"}
                           or result.get("exit_code", 0) != 0))
                textual_error = isinstance(content, str) and content.lstrip().startswith(("Error:", "Traceback", "error:"))
                confirmed = (isinstance(result, dict) and
                             (result.get("success") is True or result.get("status") in {"ok", "success", "completed"}
                              or result.get("created") is True))
                if call["name"] == "apply_patch" and isinstance(content, str) and content.startswith("Success."):
                    confirmed = True
                # A read response contains the observed bytes. Writes/edits need
                # explicit success metadata; an unrecognized response stays an attempt.
                for path, action in call["targets"]:
                    if not failed and not textual_error and (action == "read" or confirmed):
                        if isinstance(result, dict) and result.get("created") is True:
                            action = "created"
                        ledger[path][action].append({"call_event_id": call["event_id"], "result_event_id": event.id})
    return ledger, waiting


def render_ledger(ledger):
    if not ledger:
        return ""
    lines = ["FILE LEDGER (confirmed actions cite call/result events; other calls are attempts):"]
    for path, entry in ledger.items():
        confirmed = []
        for action in ("read", "edited", "created"):
            if entry[action]:
                latest = entry[action][-1]
                confirmed.append(f"{action}={latest['call_event_id']},{latest['result_event_id']}")
        status = "; ".join(confirmed) or "attempted only"
        lines.append(f"- {json.dumps(path, ensure_ascii=False)}: {status}")
    return "\n".join(lines)


def render_message(store, event, message, active, policy):
    result = dict(message)
    if not policy or not isinstance(message.get("content"), str):
        return result
    text = message["content"]
    masked = False
    try:
        with store.connect() as connection:
        # Every substituted source must be recoverable through the same scoped
        # index. Failed indexing retains the original message/capacity behavior.
            indexed = connection.execute("SELECT 1 FROM raw_event_documents WHERE event_id=?", (event["id"],)).fetchone()
            if not indexed or event.get("privacy_class", "normal") != "normal":
                return result
            if active and message.get("role") == "tool":
                view = connection.execute("SELECT mask_before_sequence FROM coding_prompt_views WHERE capsule_id=?",
                                          (active["id"],)).fetchone()
                masked = bool(view and event["sequence"] < view[0])
    except sqlite3.Error:
        return result
    oversized = estimate_tokens(text) > policy["offload_tokens"]
    if not masked and not oversized:
        return result
    original = source_text(event)
    handle = f"event:{event['id']}"
    if masked:
        result["content"] = f"OLDER TOOL OUTPUT stored as {handle}; original characters={len(original)}. Use recall or range read."
    else:
        outline = text[:320] + ("\n...\n" + text[-160:] if len(text) > 480 else "")
        result["content"] = (f"LARGE MESSAGE stored as {handle}; original characters={len(original)}. "
                             "Outline excerpts follow; use recall or range read for omitted details.\n" + outline)
    return result
