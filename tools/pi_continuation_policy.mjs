/** Pi RPC continuation: queue vs turn-start.

Pi semantics:
- `follow_up` only queues
- `prompt` starts a turn when the agent is idle

Queue acceptance is not evidence of turn start.
*/
export function continuationType(agentIdle) {
  return agentIdle ? "prompt" : "follow_up";
}

export function continuationCommand({
  agentIdle,
  id,
  message,
}) {
  return {
    id,
    type: continuationType(agentIdle),
    message,
  };
}

export function isIdleEvent(type) {
  return type === "agent_settled" || type === "agent_idle";
}

export function isBusyEvent(type) {
  return (
    type === "turn_start" ||
    type === "message_start" ||
    type === "agent_start"
  );
}

export function isTurnStartEvidence(type) {
  return type === "message_start" || type === "turn_start";
}

export function createContinuationWatchdog({
  timeoutMs = 30_000,
  now = () => Date.now(),
} = {}) {
  return {
    pending: null,
    start(record) {
      this.pending = {
        ...record,
        startedAt: now(),
        timeoutMs,
        turnStarted: false,
      };
    },
    note(type) {
      if (this.pending && isTurnStartEvidence(type)) {
        this.pending.turnStarted = true;
      }
    },
    check() {
      if (!this.pending || this.pending.turnStarted) {
        return { stalled: false, pending: this.pending };
      }
      if (now() - this.pending.startedAt < this.pending.timeoutMs) {
        return { stalled: false, pending: this.pending };
      }
      return {
        stalled: true,
        reason: "continuation_turn_start_timeout",
        pending: this.pending,
      };
    },
    clear() {
      this.pending = null;
    },
  };
}

export const postGateContinuation =
  "Continue validation work from BENCHMARK_GOAL.md. Run the test suite, fix any failures, and complete another incremental milestone slice without asking for confirmation.";

export function gateNeedsPostFinalExtension(gateStatus, minimumPostFinalTurns = 5) {
  if (!gateStatus || typeof gateStatus !== "object") return false;
  const promotions = Number(gateStatus.clean_promotions ?? gateStatus.promotions ?? 0);
  const postFinal = Number(gateStatus.post_final_promotion_turns ?? 0);
  return promotions >= 3 && postFinal < minimumPostFinalTurns;
}
