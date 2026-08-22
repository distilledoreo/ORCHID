import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { spawn } from "node:child_process";

const workspace = process.env.PREFLIGHT_WORKSPACE;
const piAgentDir = process.env.PREFLIGHT_PI_AGENT_DIR;
const sessionDir = process.env.PREFLIGHT_SESSION_DIR;
const artifactDir = process.env.PREFLIGHT_ARTIFACT_DIR;
const maxRuntimeMs = Number(process.env.PREFLIGHT_MAX_RUNTIME_MS ?? 45 * 60 * 1000);
const checkpointMs = Number(process.env.PREFLIGHT_CHECKPOINT_MS ?? 60 * 1000);
const prompt = process.env.PREFLIGHT_PROMPT;
if (!workspace || !piAgentDir || !sessionDir || !artifactDir || !prompt) {
  throw new Error("preflight environment is incomplete");
}

fs.mkdirSync(sessionDir, { recursive: true });
fs.mkdirSync(artifactDir, { recursive: true });
const startedAt = Date.now();
const rawPath = path.join(artifactDir, "pi.rpc.jsonl");
const stderrPath = path.join(artifactDir, "pi.stderr.log");
const summaryPath = path.join(artifactDir, "pi_summary.json");
const raw = fs.createWriteStream(rawPath);
const stderr = fs.createWriteStream(stderrPath);
const state = {
  started_at: new Date(startedAt).toISOString(),
  ended_at: null,
  settled: false,
  process_exit: null,
  provider_messages: 0,
  tool_calls: 0,
  tool_failures: 0,
  compaction_events: 0,
  errors: [],
  last_events: [],
};

function now() { return new Date().toISOString(); }
function note(event) {
  state.last_events.push(event.type ?? "unknown");
  if (state.last_events.length > 40) state.last_events.shift();
  if (event.type === "message_end" && event.message?.role === "assistant") state.provider_messages += 1;
  if (event.type === "tool_execution_start") state.tool_calls += 1;
  if (event.type === "tool_execution_end" && event.isError) state.tool_failures += 1;
  if (event.type === "compaction_end") state.compaction_events += 1;
  if (event.type === "agent_settled") state.settled = true;
}

async function gatewaySnapshot(reason) {
  const snapshot = { timestamp: now(), elapsed_ms: Date.now() - startedAt, reason };
  try {
    const [thread, jobs] = await Promise.all([
      fetch("http://127.0.0.1:7333/debug/thread/orchid-preflight"),
      fetch("http://127.0.0.1:7333/debug/jobs?limit=200"),
    ]);
    snapshot.thread = thread.ok ? await thread.json() : { status: thread.status };
    snapshot.jobs = jobs.ok ? await jobs.json() : { status: jobs.status };
  } catch (error) {
    snapshot.error = String(error?.message ?? error);
  }
  fs.appendFileSync(path.join(artifactDir, "telemetry.jsonl"), `${JSON.stringify(snapshot)}\n`);
}

const child = spawn("pi.cmd", [
  "--mode", "rpc",
  "--session-dir", sessionDir,
  "--name", "orchid-operability-preflight",
  "--model", "orchid/upstage/solar-pro4",
  "--thinking", "medium",
  "--no-extensions",
  "--no-skills",
  "--no-prompt-templates",
  "--no-context-files",
], {
  cwd: workspace,
  env: { ...process.env, PI_CODING_AGENT_DIR: piAgentDir },
  shell: true,
  windowsHide: true,
  stdio: ["pipe", "pipe", "pipe"],
});
child.stderr.pipe(stderr);
const lines = readline.createInterface({ input: child.stdout });
lines.on("line", (line) => {
  raw.write(`${line}\n`);
  try { note(JSON.parse(line)); }
  catch { state.errors.push({ timestamp: now(), category: "invalid_rpc_json", line: line.slice(0, 500) }); }
});
child.on("error", (error) => state.errors.push({ timestamp: now(), category: "process_error", message: String(error?.message ?? error) }));
child.on("close", (code, signal) => {
  state.process_exit = { code, signal, timestamp: now() };
});

await gatewaySnapshot("launch");
setTimeout(() => {
  if (child.stdin.writable) child.stdin.write(`${JSON.stringify({ id: "preflight-initial", type: "prompt", message: prompt })}\n`);
}, 1000);
const checkpointTimer = setInterval(() => void gatewaySnapshot("periodic"), checkpointMs);
const deadline = setTimeout(() => {
  if (child.stdin.writable) child.stdin.write(`${JSON.stringify({ type: "abort" })}\n`);
  // A provider request can ignore an RPC abort.  Do not let the benchmark
  // controller hang forever after its declared wall-clock limit.
  setTimeout(() => {
    if (!state.process_exit && child.exitCode === null) child.kill();
  }, 10_000);
}, maxRuntimeMs);

await new Promise((resolve) => {
  const poll = setInterval(() => {
    if (state.process_exit || (state.settled && Date.now() - startedAt > 5000)) {
      clearInterval(poll);
      resolve();
    }
  }, 250);
});
clearInterval(checkpointTimer);
clearTimeout(deadline);
await gatewaySnapshot(state.settled ? "agent_settled" : "process_exit");
state.ended_at = now();
fs.writeFileSync(summaryPath, JSON.stringify(state, null, 2));
raw.end();
stderr.end();
