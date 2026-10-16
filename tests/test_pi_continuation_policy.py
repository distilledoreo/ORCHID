from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tools" / "pi_continuation_policy.mjs"


def _eval(script: str) -> dict:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required for Pi continuation policy tests")
    wrapped = f"""
import {{
  continuationType,
  continuationCommand,
  gateNeedsPostFinalExtension,
  isIdleEvent,
  isBusyEvent,
  isTurnStartEvidence,
  createContinuationWatchdog,
}} from 'file:///{POLICY.as_posix()}';
{script}
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", wrapped],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_idle_agent_uses_prompt_and_expects_message_start() -> None:
    payload = _eval(
        """
const command = continuationCommand({ agentIdle: true, id: 'c1', message: 'go' });
const watchdog = createContinuationWatchdog({ timeoutMs: 1000, now: () => 0 });
watchdog.start({ rpcType: command.type, id: command.id });
watchdog.note('message_start');
console.log(JSON.stringify({ command, check: watchdog.check() }));
"""
    )
    assert payload["command"]["type"] == "prompt"
    assert payload["check"]["stalled"] is False
    assert payload["check"]["pending"]["turnStarted"] is True


def test_active_agent_uses_follow_up() -> None:
    payload = _eval(
        """
const command = continuationCommand({ agentIdle: false, id: 'c2', message: 'go' });
console.log(JSON.stringify({ command, busy: isBusyEvent('turn_start') }));
"""
    )
    assert payload["command"]["type"] == "follow_up"
    assert payload["busy"] is True


def test_agent_settled_never_sends_only_follow_up() -> None:
    payload = _eval(
        """
console.log(JSON.stringify({
  idle: isIdleEvent('agent_settled'),
  type: continuationType(true),
}));
"""
    )
    assert payload["idle"] is True
    assert payload["type"] == "prompt"


def test_follow_up_while_active_is_preserved() -> None:
    payload = _eval("console.log(JSON.stringify({ type: continuationType(false) }));")
    assert payload["type"] == "follow_up"


def test_stream_error_then_idle_uses_prompt() -> None:
    payload = _eval(
        """
const idleAfterError = isIdleEvent('agent_settled');
console.log(JSON.stringify({
  type: continuationType(idleAfterError),
  start: isTurnStartEvidence('turn_start'),
}));
"""
    )
    assert payload["type"] == "prompt"
    assert payload["start"] is True


def test_watchdog_does_not_spam_and_detects_missing_turn_start() -> None:
    payload = _eval(
        """
let t = 0;
const watchdog = createContinuationWatchdog({ timeoutMs: 50, now: () => t });
watchdog.start({ rpcType: 'prompt', id: 'c3' });
t = 60;
const first = watchdog.check();
const second = watchdog.check();
console.log(JSON.stringify({ first, second, duplicate: false }));
"""
    )
    assert payload["first"]["stalled"] is True
    assert payload["second"]["stalled"] is True
    assert payload["first"]["reason"] == "continuation_turn_start_timeout"


def test_gate_needs_post_final_extension_when_promotions_met_but_turns_short() -> None:
    payload = _eval(
        """
console.log(JSON.stringify({
  needs: gateNeedsPostFinalExtension({
    clean_promotions: 5,
    post_final_promotion_turns: 1,
  }),
  done: gateNeedsPostFinalExtension({
    clean_promotions: 5,
    post_final_promotion_turns: 5,
  }),
}));
"""
    )
    assert payload["needs"] is True
    assert payload["done"] is False
