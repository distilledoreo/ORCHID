// Sealed benchmark controller for FreetoShop Pi-vs-ORCHID all-Solar A/B.
// It sends the same initial goal and continuation text to both arms, records
// raw RPC telemetry, and deliberately performs no automatic restart or rescue.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { spawn, execFileSync } from "node:child_process";

const stage = process.env.ORCHID_ALL_SOLAR_STAGE;
const artifact = process.env.ORCHID_ALL_SOLAR_ARTIFACT;
if (!stage || !artifact) throw new Error("ORCHID_ALL_SOLAR_STAGE and ORCHID_ALL_SOLAR_ARTIFACT are required");

const maxRuntimeMs = Number(process.env.BENCHMARK_MAX_RUNTIME_MS ?? 6 * 60 * 60 * 1000);
const checkpointMs = Number(process.env.BENCHMARK_CHECKPOINT_MS ?? 15 * 60 * 1000);
const maxContinuations = Number(process.env.BENCHMARK_MAX_CONTINUATIONS ?? 32);
const sessionRoot = path.join(stage, process.env.BENCHMARK_SESSION_SUFFIX ?? "sessions");
const goal = fs.readFileSync(path.join(artifact, "BENCHMARK_GOAL.md"), "utf8");
const continuation = "Continue working through BENCHMARK_GOAL.md from the current repository state. Choose the next incomplete milestone, implement it, validate it, and do not ask for confirmation.";
const startedAt = Date.now();

fs.mkdirSync(sessionRoot, { recursive: true });
const state = new Map();
for (const [id, name, model, dirName, agentName, outputName] of [
  ["arm_a_pi", "PI_NATIVE", "openrouter/upstage/solar-pro4", "arm-a-pi", "pi-agent-a", "pi_native"],
  ["arm_b_orchid", "ORCHID_ALL_SOLAR", "orchid/upstage/solar-pro4", "arm-b-orchid", "pi-agent-b", "orchid_all_solar"],
]) {
  const output = path.join(artifact, outputName);
  fs.mkdirSync(output, { recursive: true });
  state.set(id, {
    id, name, model,
    cwd: path.join(stage, dirName),
    piAgentDir: path.join(stage, agentName),
    sessionDir: path.join(sessionRoot, id),
    rawPath: path.join(output, "telemetry.jsonl"),
    stderrPath: path.join(output, "stderr.log"),
    process: null,
    rawLog: fs.createWriteStream(path.join(output, "telemetry.jsonl"), { flags: "a" }),
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
  });
}

function now() { return new Date().toISOString(); }

function gitSnapshot(cwd) {
  try {
    return {
      head: execFileSync("git", ["rev-parse", "HEAD"], { cwd, encoding: "utf8" }).trim(),
      status: execFileSync("git", ["status", "--short"], { cwd, encoding: "utf8" }),
    };
  } catch (error) { return { error: String(error?.message ?? error) }; }
}

async function orchidSnapshot() {
  try {
    const [thread, jobs] = await Promise.all([
      fetch("http://127.0.0.1:7332/debug/thread/orchid-arm-b"),
      fetch("http://127.0.0.1:7332/debug/jobs?limit=50"),
    ]);
    return {
      thread: thread.ok ? await thread.json() : { status: thread.status },
      jobs: jobs.ok ? await jobs.json() : { status: jobs.status },
    };
  } catch (error) { return { error: String(error?.message ?? error) }; }
}

function usageFrom(value) {
  if (!value || typeof value !== "object") return null;
  const keys = ["input", "output", "totalTokens", "cacheRead", "cacheWrite", "cost"];
  return keys.some((key) => value[key] !== undefined) ? value : null;
}

function note(entry, event) {
  const type = event?.type ?? "unknown";
  entry.lastEvents.push(type);
  if (entry.lastEvents.length > 40) entry.lastEvents.shift();
  if (type === "message_end" && event.message?.role === "assistant") entry.providerMessages += 1;
  if (type === "tool_execution_start") entry.toolCalls += 1;
  if (type === "tool_execution_end" && event.isError) entry.toolFailures += 1;
  if (type === "compaction_end") entry.piCompactions += 1;
  if (type === "response") entry.requestResponses += 1;
  if (type === "message_update") entry.lastUsage = usageFrom(event.usage) ?? entry.lastUsage;
  if (type === "agent_settled") scheduleContinuation(entry);
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
    entry.continuationCount += 1;
    send(entry, { id: `${entry.id}-follow-${entry.continuationCount}`, type: "follow_up", message: continuation });
  }, 1200);
}

function launch(entry) {
  const args = [
    "--mode", "rpc",
    "--session-dir", entry.sessionDir,
    "--name", `${entry.name.toLowerCase()}-freetoshop-all-solar`,
    "--model", entry.model,
    "--thinking", "medium",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
  ];
  const child = spawn("pi.cmd", args, {
    cwd: entry.cwd,
    env: { ...process.env, PI_CODING_AGENT_DIR: entry.piAgentDir },
    shell: true,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  entry.process = child;
  entry.launchCount += 1;
  const lines = readline.createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    entry.rawLog.write(`${line}\n`);
    try { note(entry, JSON.parse(line)); }
    catch (error) { entry.errors.push({ at: now(), category: "invalid_rpc_json", error: String(error), line: line.slice(0, 500) }); }
  });
  child.stderr.pipe(entry.stderrLog, { end: false });
  child.on("error", (error) => entry.errors.push({ at: now(), category: "process_error", error: String(error?.message ?? error) }));
  child.on("close", (code, signal) => {
    lines.close();
    entry.process = null;
    entry.errors.push({ at: now(), category: "process_close", code, signal });
  });
  setTimeout(() => {
    if (entry.stopping || !entry.process || entry.process.exitCode !== null) return;
    entry.promptCount += 1;
    send(entry, { id: `${entry.id}-initial`, type: "prompt", message: goal });
  }, 500);
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
  payload.arms.arm_b_orchid.orchid = await orchidSnapshot();
  const out = path.join(artifact, "checkpoints", `checkpoint-${Date.now()}.json`);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(payload, null, 2));
}

let stopping = false;
async function stopAll(reason) {
  if (stopping) return;
  stopping = true;
  for (const entry of state.values()) {
    entry.stopping = true;
    send(entry, { type: "abort" });
  }
  await new Promise((resolve) => setTimeout(resolve, 5000));
  for (const entry of state.values()) {
    if (entry.process && entry.process.exitCode === null) entry.process.kill();
    entry.rawLog.end();
    entry.stderrLog.end();
  }
  await checkpoint(reason);
  const summary = { started_at: new Date(startedAt).toISOString(), ended_at: now(), reason, max_runtime_ms: maxRuntimeMs, arms: {} };
  for (const entry of state.values()) summary.arms[entry.id] = {
    name: entry.name, launch_count: entry.launchCount, prompt_count: entry.promptCount,
    continuation_count: entry.continuationCount, provider_messages: entry.providerMessages,
    tool_calls: entry.toolCalls, tool_failures: entry.toolFailures,
    pi_native_compactions: entry.piCompactions, request_responses: entry.requestResponses,
    errors: entry.errors, git: gitSnapshot(entry.cwd),
  };
  fs.writeFileSync(path.join(artifact, "driver_summary.json"), JSON.stringify(summary, null, 2));
}

for (const entry of state.values()) launch(entry);
await checkpoint("launch");
const checkpointTimer = setInterval(() => void checkpoint("periodic"), checkpointMs);
const deadlineTimer = setTimeout(() => void stopAll("wall_clock_limit"), maxRuntimeMs);
process.on("SIGINT", () => void stopAll("controller_interrupt"));
process.on("SIGTERM", () => void stopAll("controller_terminate"));
await new Promise((resolve) => {
  const poll = setInterval(() => { if (stopping) { clearInterval(poll); resolve(); } }, 1000);
});
clearInterval(checkpointTimer);
clearTimeout(deadlineTimer);
