import argparse
import array
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(
    description="Bounded selected-prefill interface probe; no benchmark claims"
)
parser.add_argument("--strata-root", type=Path, required=True)
parser.add_argument("--tokenizer-dir", type=Path, required=True)
parser.add_argument("--url", default="http://127.0.0.1:18585")
parser.add_argument(
    "--out", type=Path, required=True, help="Private output directory for generated responses"
)
parser.add_argument(
    "--logits-prefix", type=Path, help="Fresh STRATA_DUMP_FIRST_LOGITS prefix, optional"
)
args = parser.parse_args()
sys.path.insert(0, str(args.strata_root / "tools"))
import strata_tokenizer as ST  # noqa: E402

root = args.out
root.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(args.url.rstrip("/") + "/health", timeout=5) as response:
    health = json.load(response)
td = args.tokenizer_dir
vocab = json.loads((td / "vocab.json").read_text())
tokens = [None] * len(vocab)
for t, i in vocab.items():
    tokens[i] = t
tok = ST.Tokenizer(
    tokens,
    (td / "merges.txt").read_text().split("\n"),
    json.loads((td / "token_type.json").read_text()),
)
text = (
    "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n<|im_start|>user\n"
    + ("The marker is ORCHID. " * 40)
    + "Reply with the marker only.<|im_end|>\n<|im_start|>assistant\n"
)
ids = tok.encode(text, parse_special=True)


def call(name, body):
    t = time.monotonic()
    request = urllib.request.Request(
        args.url.rstrip("/") + "/v1/strata/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as res:
            out = json.load(res)
    except urllib.error.HTTPError as exc:
        out = {"status": exc.code, "body": exc.read().decode()}
    out["elapsed_s"] = time.monotonic() - t
    (root / (name + ".json")).write_text(json.dumps(out, indent=2))
    print(
        json.dumps(
            {
                "name": name,
                "tokens": len(ids),
                "result": out.get("token_ids"),
                "status": out.get("status", 200),
                "elapsed": out["elapsed_s"],
            }
        ),
        flush=True,
    )
    return out


full = {"token_ids": ids, "max_tokens": 8, "sampling": {"temperature": 0}}
a = call("full-native", full)
b = call(
    "full-explicit",
    {**full, "position_ids": list(range(len(ids))), "original_prompt_length": len(ids)},
)
assert a.get("token_ids") is not None and a["token_ids"] == b["token_ids"], "full retention differs"
keep = list(range(12)) + list(range(12, len(ids) - 24, 2)) + list(range(len(ids) - 24, len(ids)))
selected = [ids[i] for i in keep]
sparse = {
    "token_ids": selected,
    "max_tokens": 8,
    "sampling": {"temperature": 0},
    "position_ids": keep,
    "original_prompt_length": len(ids),
}
c = call("sparse-original", sparse)
assert c["prefill"]["position_ids"] == keep and c["prefill"]["next_position"] == len(ids)
d = call("sparse-compact", {"token_ids": selected, "max_tokens": 8, "sampling": {"temperature": 0}})
e = call("full-after-sparse", full)
assert a["token_ids"] == e["token_ids"], "position table was not reset"
for name, delta in [
    ("bad-duplicate", {"position_ids": [0] * len(selected)}),
    ("bad-context", {"original_prompt_length": health["max_context"]}),
    ("bad-token", {"token_ids": [-1] * len(selected)}),
]:
    result = call(name, {**sparse, **delta})
    assert result["status"] == 400
summary = {
    "model": a["model"],
    "context": health["max_context"],
    "prompt_tokens": len(ids),
    "selected_tokens": len(selected),
    "full_retention_token_match": a["token_ids"] == b["token_ids"],
    "ordinary_after_sparse_match": a["token_ids"] == e["token_ids"],
    "sparse_device_table_verified": c["prefill"]["device_table_verified"],
    "sparse_first_decode_position": c["prefill"]["next_position"],
    "sparse_output_differs_from_compact_control": c["token_ids"] != d["token_ids"],
    "http_rejection_checks": 3,
    "scope": "Functional interface check only; no latency or quality claim.",
}
if args.logits_prefix:

    def row(index):
        values = array.array("f")
        path = Path(str(args.logits_prefix) + "." + str(index))
        with path.open("rb") as handle:
            values.frombytes(handle.read())
        assert values and all(math.isfinite(x) for x in values), str(path)
        return values

    rows = [row(i) for i in range(5)]
    assert len({len(r) for r in rows}) == 1

    def compare(left, right):
        errors = [abs(x - y) for x, y in zip(left, right)]
        return {
            "max_abs_error": max(errors),
            "rmse": math.sqrt(sum(x * x for x in errors) / len(errors)),
            "different_values": sum(x != y for x, y in zip(left, right)),
            "values": len(left),
        }

    summary["first_logits_full_vs_explicit"] = compare(rows[0], rows[1])
    summary["first_logits_full_after_sparse"] = compare(rows[0], rows[4])
    summary["first_logits_sparse_original_vs_compact"] = compare(rows[2], rows[3])
    # Same engine/dtype: a strict 1e-5 absolute tolerance, declared before the diagnostic.
    summary["logits_checks_passed"] = (
        summary["first_logits_full_vs_explicit"]["max_abs_error"] <= 1e-5
        and summary["first_logits_full_after_sparse"]["max_abs_error"] <= 1e-5
        and summary["first_logits_sparse_original_vs_compact"]["different_values"] > 0
    )
(root / "live-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary), flush=True)

if args.logits_prefix:
    assert summary["logits_checks_passed"], (
        "Logit parity failed; inspect preserved summary and ordinary repeat control"
    )
