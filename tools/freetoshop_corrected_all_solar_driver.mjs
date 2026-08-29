// Sealed controller for the corrected FreetoShop Pi-vs-ORCHID all-Solar A/B.
// It launches each selected arm exactly once, applies identical continuation
// policy, and stops when the independent three-promotion gate requests it.
// A truncated provider stream is logged; the job stops only after Pi's sealed
// retry is exhausted. Set BENCHMARK_ARMS=orchid to run the ORCHID arm alone.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  continuationCommand,
  createContinuationWatchdog,
  gateNeedsPostFinalExtension,
  isBusyEvent,
  isIdleEvent,
  isTurnStartEvidence,
  postGateContinuation,
} from "./pi_continuation_policy.mjs";

const stage = process.env.ORCHID_CORRECTED_STAGE;
const artifact = process.env.ORCHID_CORRECTED_ARTIFACT;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const piExecutable = process.env.PI_EXECUTABLE ?? (process.platform === "win32" ? "pi.cmd" : "pi");
const pythonExecutable = process.env.PYTHON_EXECUTABLE ?? (process.platform === "win32" ? "python" : "python3");
const modelId = process.env.BENCHMARK_MODEL_ID ?? "upstage/solar-pro4";
if (!stage || !artifact) {
  throw new Error("ORCHID_CORRECTED_STAGE and ORCHID_CORRECTED_ARTIFACT are required");
}

const maxRuntimeMs = Number(process.env.BENCHMARK_MAX_RUNTIME_MS ?? 6 * 60 * 60 * 1000);
const checkpointMs = Number(process.env.BENCHMARK_CHECKPOINT_MS ?? 15 * 60 * 1000);
const maxContinuations = Number(process.env.BENCHMARK_MAX_CONTINUATIONS ?? 32);
const gatePollMs = Number(process.env.BENCHMARK_GATE_POLL_MS ?? 5000);
const enabledArms = new Set(
  String(process.env.BENCHMARK_ARMS ?? "pi,orchid")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean),
);
function armSelected(id) {
  if (id === "arm_a_pi") return enabledArms.has("pi") || enabledArms.has("pi_native") || enabledArms.has("arm_a_pi");
  if (id === "arm_b_orchid") return enabledArms.has("orchid") || enabledArms.has("arm_b_orchid");
  return false;
}
const sessionRoot = path.join(stage, "sessions");
const stopRequestPath = path.join(artifact, "controller", "STOP_REQUEST.json");
const gateStatusPath = path.join(artifact, "memory_transitions", "GATE_STATUS.json");
const goal = fs.readFileSync(path.join(artifact, "BENCHMARK_GOAL.md"), "utf8");
const continuation = "Continue working through BENCHMARK_GOAL.md from the current repository state. Choose the next incomplete milestone, implement it, validate it, and do not ask for confirmation.";
const startedAt = Date.now();

fs.mkdirSync(sessionRoot, { recursive: true });
const state = new Map();
for (const [id, name, model, directoryName, agentName, outputName] of [
  ["arm_a_pi", "PI_NATIVE", `benchmark/${modelId}`, "arm-a-pi", "pi-agent-a", "pi_native"],
  ["arm_b_orchid", "ORCHID", `orchid/${modelId}`, "arm-b-orchid", "pi-agent-b", "orchid"],
].filter(([id]) => armSelected(id))) {
  const output = path.join(artifact, outputName);
  fs.mkdirSync(output, { recursive: true });
  state.set(id, {
    id,
    name,
    model,
    cwd: path.join(stage, directoryName),
    piAgentDir: path.join(stage, agentName),
    sessionDir: path.join(sessionRoot, id),
    process: null,
    rawLog: fs.createWriteStream(path.join(output, "telemetry.jsonl"), { flags: "a" }),
    foregroundLog: fs.createWriteStream(path.join(output, "foreground_requests.jsonl"), { flags: "a" }),
    stderrLog: fs.createWriteStream(path.join(output, "stderr.log"), { flags: "a" }),
    launchCount: 0,
    promptCount: 0,
    continuationCount: 0,
    providerMessages: 0,
    toolCalls: 0,
    toolFailures: 0,
    piCompactions: 0,
    requestResponses: 0,
    errors: [],
    lastUsage: null,
    lastEvents: [],
    stopping: false,
    agentIdle: true,
    providerRetryPending: false,
    activeProviderRequest: null,
    providerRequestSequence: 0,
    watchdog: createContinuationWatchdog({ timeoutMs: Number(process.env.BENCHMARK_CONTINUATION_WATCHDOG_MS ?? 30_000) }),
  });
}
if (state.size === 0) {
  throw new Error("BENCHMARK_ARMS selected no arms");
}

function now() {
  return new Date().toISOString();
}

function gitSnapshot(cwd) {
  try {
    return {
      head: execFileSync("git", ["rev-parse", "HEAD"], { cwd, encoding: "utf8" }).trim(),
      status: execFileSync("git", ["status", "--short"], { cwd, encoding: "utf8" }),
    };
  } catch (error) {
    return { error: String(error?.message ?? error) };
  }
}

function readGateStatus() {
  try {
    if (!fs.existsSync(gateStatusPath)) return null;
    return JSON.parse(fs.readFileSync(gateStatusPath, "utf8"));
  } catch {
    return null;
  }
}

function continuationMessageFor(entry) {
  const gate = readGateStatus();
  if (gateNeedsPostFinalExtension(gate)) {
    return postGateContinuation;
  }
  return continuation;
}

async function orchidSnapshot() {
  try {
    const [thread, jobs] = await Promise.all([
      fetch("http://127.0.0.1:7332/debug/thread/orchid-arm-b"),
      fetch("http://127.0.0.1:7332/debug/jobs?limit=100"),
    ]);
    return {
      thread: thread.ok ? await thread.json() : { status: thread.status },
      jobs: jobs.ok ? await jobs.json() : { status: jobs.status },
    };
  } catch (error) {
    return { error: String(error?.message ?? error) };
  }
}

function usageFrom(value) {
  if (!value || typeof value !== "object") return null;
  const keys = ["input", "output", "totalTokens", "cacheRead", "cacheWrite", "cost"];
  return keys.some((key) => value[key] !== undefined) ? value : null;
}

function send(entry, command) {
  if (!entry.process?.stdin?.writable) return false;
  entry.process.stdin.write(`${JSON.stringify(command)}\n`);
  return true;
}

function scheduleContinuation(entry) {
  if (entry.stopping || entry.continuationCount >= maxContinuations) return;
  setTimeout(() => {
    if (entry.stopping || !entry.process || entry.process.exitCode !== null) return;
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
        return;
      }
    }
    entry.continuationCount += 1;
    const command = continuationCommand({
      agentIdle: entry.agentIdle,
      id: `${entry.id}-continue-${entry.continuationCount}`,
      message: continuationMessageFor(entry),
    });
    if (command.type === "prompt") entry.promptCount += 1;
    entry.watchdog.start({ rpcType: command.type, id: command.id, arm: entry.id });
    send(entry, command);
  }, 1200);
}

function note(entry, event) {
  const observedAtMs = Date.now();
  const type = event?.type ?? "unknown";
  entry.lastEvents.push(type);
  if (entry.lastEvents.length > 40) entry.lastEvents.shift();
  if (isBusyEvent(type)) entry.agentIdle = false;
  if (isIdleEvent(type)) entry.agentIdle = true;
  entry.watchdog.note(type);
  if (entry.watchdog.pending?.turnStarted && isTurnStartEvidence(type)) {
    entry.watchdog.clear();
  }
  if (type === "message_start" && event.message?.role === "assistant") {
    entry.providerRequestSequence += 1;
    entry.activeProviderRequest = {
      schema_version: "orchid.direct_foreground_latency.v1",
      arm: entry.name,
      sequence: entry.providerRequestSequence,
      provider_message_timestamp_ms: event.message?.timestamp ?? null,
      started_at_ms: observedAtMs,
      first_stream_event_at_ms: null,
    };
  }
  if (
    type === "message_update"
    && entry.activeProviderRequest
    && entry.activeProviderRequest.first_stream_event_at_ms === null
  ) {
    entry.activeProviderRequest.first_stream_event_at_ms = observedAtMs;
  }
  if (type === "message_end" && event.message?.role === "assistant") {
    const request = entry.activeProviderRequest;
    if (request) {
      const firstEventAt = request.first_stream_event_at_ms;
      entry.foregroundLog.write(`${JSON.stringify({
        ...request,
        ended_at_ms: observedAtMs,
        provider_wall_ms: observedAtMs - request.started_at_ms,
        time_to_first_event_ms: firstEventAt === null ? null : firstEventAt - request.started_at_ms,
        stop_reason: event.message?.stopReason ?? null,
        success: event.message?.stopReason !== "error",
      })}\n`);
      entry.activeProviderRequest = null;
    }
    entry.providerMessages += 1;
    if (event.message.stopReason === "error") {
      entry.providerRetryPending = true;
      entry.errors.push({
        at: now(),
        category: "provider_response_error",
        error: event.message.errorMessage ?? "unknown provider error",
      });
    } else {
      entry.providerRetryPending = false;
    }
  }
  if (type === "auto_retry_start") {
    entry.providerRetryPending = true;
  }
  if (type === "auto_retry_end") {
    const exhausted = event.success === false;
    entry.providerRetryPending = false;
    if (exhausted) {
      void stopAll(`${entry.id}_provider_retry_exhausted`);
    }
  }
  if (type === "agent_end" && event.willRetry === false && entry.providerRetryPending) {
    entry.providerRetryPending = false;
    void stopAll(`${entry.id}_provider_retry_exhausted`);
  }
  if (type === "tool_execution_start") entry.toolCalls += 1;
  if (type === "tool_execution_end" && event.isError) entry.toolFailures += 1;
  if (type === "compaction_end") entry.piCompactions += 1;
  if (type === "response") entry.requestResponses += 1;
  if (type === "message_update") entry.lastUsage = usageFrom(event.usage) ?? entry.lastUsage;
  if (type === "agent_settled") scheduleContinuation(entry);
}

const sequentialArms = process.env.BENCHMARK_SEQUENTIAL_ARMS === "1";
const activeArmPath = path.join(artifact, "hardware", "active_arm.txt");
const launchOrder = sequentialArms
  ? [...state.values()].sort((left, right) => Number(right.id === "arm_b_orchid") - Number(left.id === "arm_b_orchid"))
  : [...state.values()];
let sequentialIndex = 0;
let orchidElapsedMs = null;
let matchedPiTimer = null;
let finishingArm = false;
let gateConsumed = false;

function writeActiveArm(name) {
  fs.mkdirSync(path.join(artifact, "hardware"), { recursive: true });
  fs.writeFileSync(activeArmPath, `${name}\n`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function currentSequentialEntry() {
  return launchOrder[sequentialIndex] ?? null;
}

let stopping = false;
let monitorProcess = null;

function launch(entry) {
  const arguments_ = [
    "--mode", "rpc",
    "--session-dir", entry.sessionDir,
    "--name", `${entry.name.toLowerCase()}-freetoshop-corrected-all-solar`,
    "--model", entry.model,
    "--thinking", "medium",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
  ];
  const child = spawn(piExecutable, arguments_, {
    cwd: entry.cwd,
    env: { ...process.env, PI_CODING_AGENT_DIR: entry.piAgentDir },
    shell: process.platform === "win32",
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  entry.process = child;
  entry.launchCount += 1;
  entry.agentIdle = true;
  entry.watchdog.clear();
  const lines = readline.createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    entry.rawLog.write(`${line}\n`);
    try {
      note(entry, JSON.parse(line));
    } catch (error) {
      entry.errors.push({
        at: now(),
        category: "invalid_rpc_json",
        error: String(error),
        line: line.slice(0, 500),
      });
    }
  });
  child.stderr.pipe(entry.stderrLog, { end: false });
  child.on("error", (error) => {
    entry.errors.push({ at: now(), category: "process_error", error: String(error?.message ?? error) });
  });
  child.on("close", (code, signal) => {
    lines.close();
    entry.process = null;
    if (!stopping && !entry.stopping) {
      entry.errors.push({ at: now(), category: "unexpected_process_close", code, signal });
      void stopAll(`${entry.id}_unexpected_process_exit`);
    }
  });
  setTimeout(() => {
    if (entry.stopping || !entry.process || entry.process.exitCode !== null) return;
    entry.promptCount += 1;
    send(entry, { id: `${entry.id}-initial`, type: "prompt", message: goal });
  }, 500);
}

function launchMonitor() {
  const stderr = fs.createWriteStream(path.join(artifact, "controller", "monitor.stderr.log"), { flags: "a" });
  const stdout = fs.createWriteStream(path.join(artifact, "controller", "monitor.stdout.log"), { flags: "a" });
  monitorProcess = spawn(
    pythonExecutable,
    [path.join("tools", "benchmark_freetoshop_corrected_all_solar.py"), "monitor", "--interval-seconds", "5"],
    { cwd: root, env: process.env, windowsHide: true },
  );
  monitorProcess.stdout.pipe(stdout);
  monitorProcess.stderr.pipe(stderr);
  monitorProcess.on("close", (code, signal) => {
    stdout.end();
    stderr.end();
    if (!stopping) void stopAll(`gate_monitor_unexpected_exit_${code ?? signal}`);
  });
}

async function checkpoint(reason) {
  const payload = { timestamp: now(), elapsed_ms: Date.now() - startedAt, reason, arms: {} };
  for (const entry of state.values()) {
    payload.arms[entry.id] = {
      name: entry.name,
      pid: entry.process?.pid ?? null,
      launch_count: entry.launchCount,
      prompt_count: entry.promptCount,
      continuation_count: entry.continuationCount,
      provider_messages: entry.providerMessages,
      tool_calls: entry.toolCalls,
      tool_failures: entry.toolFailures,
      pi_native_compactions: entry.piCompactions,
      request_responses: entry.requestResponses,
      errors: entry.errors.slice(-20),
      last_usage: entry.lastUsage,
      last_events: entry.lastEvents,
      git: gitSnapshot(entry.cwd),
    };
  }
  if (state.has("arm_b_orchid")) {
    payload.arms.arm_b_orchid.orchid = await orchidSnapshot();
  }
  const output = path.join(artifact, "checkpoints", `checkpoint-${Date.now()}.json`);
  fs.writeFileSync(output, JSON.stringify(payload, null, 2));
}

function terminateOwnedTree(child) {
  if (!child || child.exitCode !== null || !child.pid) return;
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
    } else {
      child.kill("SIGTERM");
    }
  } catch {
    // The process may have exited after the check.
  }
}

function stopGateway() {
  const pidPath = path.join(artifact, "gateway", "PID.json");
  if (!fs.existsSync(pidPath)) return;
  try {
    const gatewayPid = Number(JSON.parse(fs.readFileSync(pidPath, "utf8")).pid);
    if (Number.isInteger(gatewayPid) && gatewayPid > 0) {
      if (process.platform === "win32") {
        execFileSync("taskkill.exe", ["/PID", String(gatewayPid), "/T", "/F"], { windowsHide: true });
      } else {
        process.kill(gatewayPid, "SIGTERM");
      }
    }
  } catch {
    // Exact owned gateway may already have exited.
  }
}

async function finishSequentialArm(reason) {
  if (finishingArm || stopping) return;
  const entry = currentSequentialEntry();
  if (!entry) {
    await stopAll(reason);
    return;
  }
  finishingArm = true;
  entry.stopping = true;
  send(entry, { type: "abort" });
  await sleep(5000);
  terminateOwnedTree(entry.process);
  entry.process = null;
  const elapsed = Date.now() - (entry.armStartedAt ?? startedAt);
  if (entry.id === "arm_b_orchid") orchidElapsedMs = elapsed;
  sequentialIndex += 1;
  finishingArm = false;
  const next = currentSequentialEntry();
  if (!next) {
    await stopAll(reason);
    return;
  }
  next.armStartedAt = Date.now();
  writeActiveArm(next.name);
  launch(next);
  if (next.id === "arm_a_pi" && orchidElapsedMs != null) {
    matchedPiTimer = setTimeout(() => {
      void requestStop("matched_orchid_duration");
    }, orchidElapsedMs);
  }
}

async function requestStop(reason) {
  if (sequentialArms && !stopping && currentSequentialEntry()) {
    await finishSequentialArm(reason);
    return;
  }
  await stopAll(reason);
}

async function stopAll(reason) {
  if (stopping) return;
  stopping = true;
  if (matchedPiTimer) clearTimeout(matchedPiTimer);
  for (const entry of state.values()) {
    entry.stopping = true;
    send(entry, { type: "abort" });
  }
  await new Promise((resolve) => setTimeout(resolve, 5000));
  await checkpoint(reason);
  for (const entry of state.values()) {
    terminateOwnedTree(entry.process);
    entry.rawLog.end();
    entry.foregroundLog.end();
    entry.stderrLog.end();
  }
  if (monitorProcess?.exitCode === null) monitorProcess.kill();
  stopGateway();
  const summary = {
    started_at: new Date(startedAt).toISOString(),
    ended_at: now(),
    reason,
    max_runtime_ms: maxRuntimeMs,
    sequential_arms: sequentialArms,
    orchid_elapsed_ms: orchidElapsedMs,
    arms: {},
  };
  for (const entry of state.values()) {
    summary.arms[entry.id] = {
      name: entry.name,
      launch_count: entry.launchCount,
      prompt_count: entry.promptCount,
      continuation_count: entry.continuationCount,
      provider_messages: entry.providerMessages,
      tool_calls: entry.toolCalls,
      tool_failures: entry.toolFailures,
      pi_native_compactions: entry.piCompactions,
      request_responses: entry.requestResponses,
      errors: entry.errors,
      git: gitSnapshot(entry.cwd),
    };
  }
  fs.writeFileSync(path.join(artifact, "driver_summary.json"), JSON.stringify(summary, null, 2));
}

launchMonitor();
if (sequentialArms) {
  const first = launchOrder[0];
  first.armStartedAt = Date.now();
  writeActiveArm(first.name);
  launch(first);
} else {
  writeActiveArm("SHARED");
  for (const entry of launchOrder) launch(entry);
}
await checkpoint("launch");
const checkpointTimer = setInterval(() => void checkpoint("periodic"), checkpointMs);
const gateTimer = setInterval(() => {
  if (!fs.existsSync(stopRequestPath) || gateConsumed) return;
  try {
    const request = JSON.parse(fs.readFileSync(stopRequestPath, "utf8"));
    gateConsumed = true;
    void requestStop(request.reason ?? "gate_stop_request");
  } catch (error) {
    void stopAll(`invalid_gate_stop_request_${String(error)}`);
  }
}, gatePollMs);
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
clearInterval(checkpointTimer);
clearInterval(gateTimer);
clearTimeout(deadlineTimer);
