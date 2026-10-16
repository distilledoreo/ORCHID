"""Bounded smoke test; requires explicit local models and isolated server configs."""

import argparse
import gc
import hashlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .core import compatible_ids, token_hash, validate_input


def post(url, body):
    request = urllib.request.Request(
        url + "/v1/strata/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def load_frontend(root, tokenizer_dir):
    sys.path[:0] = [str(root), str(root / "tools")]
    import strata_tokenizer as st
    from serve.frontend import ChatTemplate

    vocab = json.loads((tokenizer_dir / "vocab.json").read_text())
    tokens = [None] * len(vocab)
    for text, index in vocab.items():
        tokens[index] = text
    tok = st.Tokenizer(
        tokens,
        (tokenizer_dir / "merges.txt").read_text().split("\n"),
        json.loads((tokenizer_dir / "token_type.json").read_text()),
    )
    path = tokenizer_dir / "chat_template.jinja"
    template = ChatTemplate(path if path.exists() else root / "serve/chat_template.jinja")
    return tok, template, vocab


def prepare(case, tok, template):
    text = template.render(case["messages"], enable_thinking=False)
    return tok.encode(text, parse_special=True)


def cases(tok, template):
    result = []
    for size in (512, 1536):
        for placement, answer in ((0.2, "KELP47"), (0.8, "FERN83")):
            rows = []
            while True:
                rows.append(f"Record D{len(rows):03d} has access code WILLOW{len(rows):03d}.")
                messages = [
                    {
                        "role": "system",
                        "content": "Read the records and answer the question exactly.",
                    },
                    {
                        "role": "user",
                        "content": "\n".join(rows)
                        + "\nWhat is the access code for record R17? Reply with only its code.",
                    },
                ]
                case = {"messages": messages}
                if len(prepare(case, tok, template)) >= size - 24:
                    break
            rows.insert(int(len(rows) * placement), f"Record R17 has access code {answer}.")
            messages[1]["content"] = (
                "\n".join(rows) + messages[1]["content"][messages[1]["content"].rfind("\nWhat") :]
            )
            case = {
                "id": f"tokens{size}-at{int(placement * 100)}",
                "messages": messages,
                "answer": answer,
            }
            case["input_ids"] = prepare(case, tok, template)
            case["input_hash"] = token_hash(case["input_ids"])
            result.append(case)
    return result


class Server:
    def __init__(self, args, config, label):
        self.args, self.config, self.label = args, config, label
        self.proc = None

    def __enter__(self):
        # Do not replace a service that happens to own the requested port.
        import socket

        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.args.port))
        self.log = (self.args.output / (self.label + "-server.log")).open("w")
        env = dict(os.environ, STRATA_ROUTE_TAIL_SKIP="0", STRATA_PREFILL_CPU_SHARE="0")
        self.proc = subprocess.Popen(
            [
                str(self.args.server_python),
                str(self.args.strata_root / "serve/server.py"),
                "--engine",
                "strata",
                "--config",
                str(self.config),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.args.port),
            ],
            stdout=self.log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"{self.label} server exited: inspect its private log")
                try:
                    with urllib.request.urlopen(self.args.url + "/health", timeout=1) as response:
                        health = json.load(response)
                    if health.get("loaded"):
                        self.health = health
                        return self
                except OSError:
                    pass
                time.sleep(1)
            raise RuntimeError("Isolated server did not become ready within five minutes")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGINT)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self.log.close()


def run_arm(args, case, arm, epoch, tok, template, student=None):
    started = (
        time.perf_counter()
    )  # includes fresh template/tokenization, student work, selection, API call
    ids = prepare(case, tok, template)
    if ids != case["input_ids"]:
        raise RuntimeError("Frozen prompt IDs changed")
    trace = None
    body = {"token_ids": ids, "max_tokens": 24, "sampling": {"temperature": 0}}
    if arm != "full":
        trace = student.trace(ids, 1.0 if arm == "keep100" else 0.5)
        positions = trace["positions"]
        body.update(
            token_ids=[ids[p] for p in positions],
            position_ids=positions,
            original_prompt_length=len(ids),
        )
    request_at = time.perf_counter()
    response = post(args.url, body)
    ended = time.perf_counter()
    if trace and (
        response["prefill"]["token_ids"] != body["token_ids"]
        or response["prefill"]["position_ids"] != positions
        or response["prefill"]["next_position"] != len(ids)
        or not response["prefill"]["device_table_verified"]
    ):
        raise RuntimeError("Target device boundary differs from selected original IDs/positions")
    # Both processes run on this host: perf_counter uses the same monotonic clock.
    # This includes HTTP/validation overhead and fresh student work; no reused trace.
    server_ttft = response["timings"]["ttft_s"]
    first = response["timings"].get("first_token_monotonic_s")
    if first is None or not started <= first <= ended:
        raise RuntimeError("Missing or incompatible same-host first-token clock")
    text = tok.decode(response["token_ids"])
    words = re.findall(r"[A-Za-z]+\d+", re.sub(r"<\|[^>]+\|>", "", text))
    return {
        "epoch": epoch,
        "case": case["id"],
        "arm": arm,
        "input_hash": token_hash(ids),
        "original_tokens": len(ids),
        "retained_tokens": len(body["token_ids"]),
        "output_ids": response["token_ids"],
        "output_text": text,
        "correct": words == [case["answer"]],
        "trace": trace,
        "pre_request_s": request_at - started,
        "pipeline_ttft_s": first - started,
        "server_ttft_s": server_ttft,
        "total_s": ended - started,
        "network_roundtrip_s": ended - request_at,
        "response": response,
        "ttft_definition": "same-host monotonic first-token time minus client start before preparation",
    }


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--strata-root", type=Path, required=True)
    parser.add_argument("--server-python", type=Path, required=True)
    parser.add_argument("--student", type=Path, required=True)
    parser.add_argument("--alone-config", type=Path, required=True)
    parser.add_argument("--colocated-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18586)
    args = parser.parse_args()
    args.url = f"http://127.0.0.1:{args.port}"
    args.output.mkdir(parents=True, exist_ok=False)
    alone, colocated = [
        json.loads(p.read_text()) for p in (args.alone_config, args.colocated_config)
    ]
    if alone["tokenizer"] != colocated["tokenizer"]:
        raise ValueError("Both resource epochs must use the same tokenizer")

    def model_args(config):
        argv = list(config["args"])
        index = argv.index("--vram-reserve-mib")
        del argv[index : index + 2]
        return argv

    if alone["exe"] != colocated["exe"] or model_args(alone) != model_args(colocated):
        raise ValueError("Resource epochs may differ only in VRAM reserve and log paths")
    tok, template, vocab = load_frontend(args.strata_root, Path(alone["tokenizer"]))
    allowed = compatible_ids(vocab, json.loads((args.student / "tokenizer.json").read_text()))
    frozen = cases(tok, template)
    for case in frozen:
        validate_input(case["input_ids"], allowed)
    plan = {
        "cases": frozen,
        "seed": 731,
        "chunk_size": 32,
        "pool_kernel": 13,
        "lookahead_query_rows_max": 8,
        "ratios": [1.0, 0.5],
        "max_output_tokens": 24,
        "student": str(args.student),
        "main_configs": [alone, colocated],
        "reference_commit": "c1d6bd323efd1dc11a2ba85bc49f52879db43c7b",
        "status": "frozen_before_model_calls",
        "shared_ids": len(allowed),
        "limitations": "Four synthetic functional cases; hybrid models; sequential resource epochs; no paper latency reproduction.",
    }
    plan["sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    (args.output / "protocol.json").write_text(json.dumps(plan, indent=2))
    results = []

    def collect(case, arm, epoch, student=None):
        result = run_arm(args, case, arm, epoch, tok, template, student)
        results.append(result)
        with (args.output / "requests.jsonl").open("a") as handle:
            handle.write(json.dumps(result) + "\n")
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "epoch",
                        "case",
                        "arm",
                        "retained_tokens",
                        "correct",
                        "pipeline_ttft_s",
                    )
                }
            ),
            flush=True,
        )

    # Full prefill gets its own resource epoch without student weights or a student VRAM reserve.
    with Server(args, args.alone_config, "standalone"):
        warm = run_arm(args, frozen[0], "full", "warmup", tok, template)
        (args.output / "standalone-warmup.json").write_text(json.dumps(warm))
        for case in frozen:
            collect(case, "full", "standalone")
    from .student import Student

    with Server(args, args.colocated_config, "colocated"):
        student = Student(args.student, allowed)
        warmups = [
            run_arm(args, frozen[0], arm, "warmup", tok, template, student)
            for arm in ("full", "keep100", "keep50")
        ]
        (args.output / "colocated-warmups.json").write_text(json.dumps(warmups))
        if warmups[0]["output_ids"] != warmups[1]["output_ids"]:
            raise RuntimeError(
                "100% retention failed generation equivalence; stop before comparing sparse prompts"
            )
        tasks = [(case, arm) for case in frozen for arm in ("full", "keep100", "keep50")]
        random.Random(731).shuffle(tasks)
        for case, arm in tasks:
            collect(case, arm, "colocated", student)
        del student
        gc.collect()
    # Every full-retention result must match the same case's ordinary colocated result.
    for case in frozen:
        rows = {
            r["arm"]: r for r in results if r["case"] == case["id"] and r["epoch"] == "colocated"
        }
        if rows["full"]["output_ids"] != rows["keep100"]["output_ids"]:
            raise RuntimeError(
                "100% retention mismatch: preserve results without a latency conclusion"
            )
    (args.output / "COMPLETE.json").write_text(
        json.dumps({"requests": len(results), "protocol_sha256": plan["sha256"]})
    )


if __name__ == "__main__":
    main()
