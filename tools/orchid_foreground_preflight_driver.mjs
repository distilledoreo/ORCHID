// ORCHID-only short live preflight for foreground substitution.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  continuationCommand,
  createContinuationWatchdog,
  isBusyEvent,
  isIdleEvent,
  isTurnStartEvidence,
} from "./pi_continuation_policy.mjs";

const stage = process.env.ORCHID_PREFLIGHT_STAGE;
const artifact = process.env.ORCHID_PREFLIGHT_ARTIFACT;
const gatewayUrl = process.env.ORCHID_PREFLIGHT_GATEWAY_URL ?? "http://127.0.0.1:7334";
const threadId = process.env.ORCHID_PREFLIGHT_THREAD_ID ?? "orchid-preflight";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
if (!stage || !artifact) {
  throw new Error("ORCHID_PREFLIGHT_STAGE and ORCHID_PREFLIGHT_ARTIFACT are required");
}

const maxRuntimeMs = Number(process.env.PREFLIGHT_MAX_RUNTIME_MS ?? 90 * 60 * 1000);
const requiredPromotions = Number(process.env.PREFLIGHT_REQUIRED_PROMOTIONS ?? 3);
const requiredPostTurns = Number(process.env.PREFLIGHT_REQUIRED_POST_TURNS ?? 5);
const goal = fs.readFileSync(path.join(artifact, "BENCHMARK_GOAL.md"), "utf8");
const continuation = "Continue working through BENCHMARK_GOAL.md from the current repository state. Choose the next incomplete milestone, implement it, validate it, and do not ask for confirmation.";
const startedAt = Date.now();
const sessionDir = path.join(stage, "sessions", "orchid-preflight");
fs.mkdirSync(sessionDir, { recursive: true });
fs.mkdirSync(path.join(artifact, "controller"), { recursive: true });

const entry = {
  cwd: path.join(stage, "arm-b-orchid"),
  piAgentDir: path.join(stage, "pi-agent-preflight"),
  process: null,
  rawLog: fs.createWriteStream(path.join(artifact, "pi.rpc.jsonl"), { flags: "a" }),
  stderrLog: fs.createWriteStream(path.join(artifact, "pi.stderr.log"), { flags: "a" }),
  promptCount: 0,
  continuationCount: 0,
  providerMessages: 0,
  toolCalls: 0,
  toolFailures: 0,
  errors: [],
  lastEvents: [],
  stopping: false,
  agentIdle: true,
  lastRpcType: null,
  rpcLog: [],
  watchdog: createContinuationWatchdog({ timeoutMs: Number(process.env.PREFLIGHT_CONTINUATION_WATCHDOG_MS ?? 30_000) }),
};

let stopping = false;
let stopReason = null;
let postPromotionTurns = 0;
let stopCompletion = null;

function now() {
  return new Date().toISOString();
}

function send(command) {
  if (!entry.process?.stdin?.writable) return false;
  entry.lastRpcType = command.type;
  entry.rpcLog.push({ at: now(), type: command.type, id: command.id ?? null });
  entry.process.stdin.write(`${JSON.stringify(command)}\n`);
  return true;
}

async function orchidSnapshot() {
  try {
    const [thread, jobs] = await Promise.all([
      fetch(`${gatewayUrl}/debug/thread/${threadId}`),
      fetch(`${gatewayUrl}/debug/jobs?limit=100`),
    ]);
    return {
      thread: thread.ok ? await thread.json() : { status: thread.status },
      jobs: jobs.ok ? await jobs.json() : { status: jobs.status },
    };
  } catch (error) {
    return { error: String(error?.message ?? error) };
  }
}

function promotedCount(snapshot) {
  const jobs = snapshot?.jobs?.jobs ?? snapshot?.thread?.jobs ?? [];
  if (!Array.isArray(jobs)) return 0;
  return jobs.filter((job) => job.status === "PROMOTED").length;
}

function scheduleContinuation() {
  if (entry.stopping || stopping) return;
  setTimeout(() => {
    if (entry.stopping || stopping || !entry.process || entry.process.exitCode !== null) return;
    if (entry.watchdog.pending && !entry.watchdog.pending.turnStarted) {
      const stall = entry.watchdog.check();
      if (stall.stalled) {
        entry.errors.push({
          at: now(),
          category: "continuation_turn_start_timeout",
          rpc_type: stall.pending.rpcType,
          id: stall.pending.id,
        });
        entry.watchdog.clear();
        void stopAll("controller_stall_no_turn_start");
        return;
      }
    }
    entry.continuationCount += 1;
    const command = continuationCommand({
      agentIdle: entry.agentIdle,
      id: `preflight-continue-${entry.continuationCount}`,
      message: continuation,
    });
    if (command.type === "prompt") entry.promptCount += 1;
    entry.watchdog.start({ rpcType: command.type, id: command.id });
    send(command);
  }, 1200);
}

function note(event) {
  const type = event?.type ?? "unknown";
  entry.lastEvents.push(type);
  if (entry.lastEvents.length > 40) entry.lastEvents.shift();
  if (isBusyEvent(type)) entry.agentIdle = false;
  if (isIdleEvent(type)) entry.agentIdle = true;
  entry.watchdog.note(type);
  if (entry.watchdog.pending?.turnStarted && isTurnStartEvidence(type)) {
    entry.watchdog.clear();
  }
  if (type === "message_end" && event.message?.role === "assistant") {
    entry.providerMessages += 1;
    void orchidSnapshot().then((snapshot) => {
      const promoted = promotedCount(snapshot);
      if (promoted >= requiredPromotions) {
        postPromotionTurns += 1;
        fs.appendFileSync(
          path.join(artifact, "controller", "turn_gate.jsonl"),
          `${JSON.stringify({ at: now(), promoted, post_promotion_turns: postPromotionTurns, last_rpc: entry.lastRpcType })}\n`,
        );
        if (postPromotionTurns >= requiredPostTurns) {
          void stopAll("preflight_gate_satisfied");
        }
      }
    });
  }
  if (type === "tool_execution_start") entry.toolCalls += 1;
  if (type === "tool_execution_end" && event.isError) entry.toolFailures += 1;
  if (type === "agent_settled") scheduleContinuation();
}

function launch() {
  const child = spawn("pi.cmd", [
    "--mode", "rpc",
    "--session-dir", sessionDir,
    "--name", "orchid-foreground-substitution-preflight",
    "--model", "orchid/upstage/solar-pro4",
    "--thinking", "medium",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
  ], {
    cwd: entry.cwd,
    env: { ...process.env, PI_CODING_AGENT_DIR: entry.piAgentDir },
    shell: true,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  entry.process = child;
  entry.agentIdle = true;
  const lines = readline.createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    entry.rawLog.write(`${line}\n`);
    try {
      note(JSON.parse(line));
    } catch (error) {
      entry.errors.push({ at: now(), category: "invalid_rpc_json", error: String(error), line: line.slice(0, 400) });
    }
  });
  child.stderr.pipe(entry.stderrLog, { end: false });
  child.on("error", (error) => entry.errors.push({ at: now(), category: "process_error", error: String(error?.message ?? error) }));
  child.on("close", (code, signal) => {
    lines.close();
    entry.process = null;
    if (!stopping) {
      entry.errors.push({ at: now(), category: "unexpected_process_close", code, signal });
      void stopAll("unexpected_process_exit");
    }
  });
  setTimeout(() => {
    if (entry.stopping || !entry.process) return;
    entry.promptCount += 1;
    send({ id: "preflight-initial", type: "prompt", message: goal });
  }, 500);
}

function terminateOwnedTree(child) {
  if (!child || child.exitCode !== null || !child.pid) return;
  try {
    execFileSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
  } catch {
    // already gone
  }
}

function stopGateway() {
  const pidPath = path.join(artifact, "gateway", "PID.json");
  if (!fs.existsSync(pidPath)) return;
  try {
    const gatewayPid = Number(JSON.parse(fs.readFileSync(pidPath, "utf8")).pid);
    if (Number.isInteger(gatewayPid) && gatewayPid > 0) {
      execFileSync("taskkill.exe", ["/PID", String(gatewayPid), "/T", "/F"], { windowsHide: true });
    }
  } catch {
    // already gone
  }
}

async function checkpoint(reason) {
  const snapshot = await orchidSnapshot();
  const payload = {
    timestamp: now(),
    elapsed_ms: Date.now() - startedAt,
    reason,
    prompt_count: entry.promptCount,
    continuation_count: entry.continuationCount,
    provider_messages: entry.providerMessages,
    post_promotion_turns: postPromotionTurns,
    last_rpc_type: entry.lastRpcType,
    agent_idle: entry.agentIdle,
    errors: entry.errors.slice(-20),
    last_events: entry.lastEvents,
    orchid: snapshot,
  };
  fs.writeFileSync(
    path.join(artifact, "controller", `checkpoint-${Date.now()}.json`),
    JSON.stringify(payload, null, 2),
  );
  return snapshot;
}

async function finishStop(reason) {
  stopping = true;
  stopReason = reason;
  entry.stopping = true;
  send({ type: "abort" });
  await new Promise((resolve) => setTimeout(resolve, 4000));
  const snapshot = await checkpoint(reason);
  terminateOwnedTree(entry.process);
  entry.rawLog.end();
  entry.stderrLog.end();
  stopGateway();
  fs.writeFileSync(path.join(artifact, "driver_summary.json"), JSON.stringify({
    started_at: new Date(startedAt).toISOString(),
    ended_at: now(),
    reason,
    prompt_count: entry.promptCount,
    continuation_count: entry.continuationCount,
    provider_messages: entry.providerMessages,
    post_promotion_turns: postPromotionTurns,
    rpc_log: entry.rpcLog,
    errors: entry.errors,
    orchid: snapshot,
  }, null, 2));
}

function stopAll(reason) {
  if (!stopCompletion) stopCompletion = finishStop(reason);
  return stopCompletion;
}

launch();
await checkpoint("launch");
const checkpointTimer = setInterval(() => void checkpoint("periodic"), 60_000);
const deadlineTimer = setTimeout(() => void stopAll("wall_clock_limit"), maxRuntimeMs);
process.on("SIGINT", () => void stopAll("controller_interrupt"));
process.on("SIGTERM", () => void stopAll("controller_terminate"));
await new Promise((resolve) => {
  const poll = setInterval(() => {
    if (stopping) {
      clearInterval(poll);
      resolve();
    }
  }, 1000);
});
if (stopCompletion) await stopCompletion;
clearInterval(checkpointTimer);
clearTimeout(deadlineTimer);

try {
  execFileSync("python", [path.join("tools", "orchid_foreground_preflight_report.py"), artifact], {
    cwd: root,
    windowsHide: true,
    stdio: "inherit",
  });
} catch {
  // report writes its own failure file
}
