"""Build frozen per-turn session sets: cumulative history + probe, tokenized.

Frozen template rule (byte-validated: rebuild with seed 20261019 reproduces
session-sets-v1 input_ids exactly for early_late, task_shift, multifile-t23):
- prompt = render(msgs[:after_msg+1]); a q-separate probe appends the
  assistant ack that follows the anchor in the session (the ack the model
  would have produced before the user asks the follow-up) then the probe
  question as a user turn; q=None probes append nothing.
- each turn renders as <|im_start|>{role}\\n{content}<|im_end|>\\n, prompt ends
  with the <|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n generation prefix.

Usage (Strata venv for strata_tokenizer):
  .../Strata/.venv/bin/python -m experiments.strata_specprefill.build_session_sets \\
    --seed 20261107 --prefix ses3- --output data/strata_specprefill/session-sets-v3.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PREFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def render(msgs: list[dict]) -> str:
    # Assistant turns always carry the think block (byte-matches the frozen
    # v1/v2 sets); the prompt ends with the same block as generation prefix.
    out = []
    for m in msgs:
        if m["role"] == "assistant":
            out.append("<|im_start|>assistant\n<think>\n\n</think>\n\n"
                       + f"{m['content']}<|im_end|>\n")
        else:
            out.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n")
    out.append(PREFIX)
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--prefix", default="ses3-")
    ap.add_argument("--output", type=Path,
                    default=ROOT / "data/strata_specprefill/session-sets-v3.json")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, "/home/distilledoreo/AI/Strata/tools")
    sys.path.insert(0, "/home/distilledoreo/AI/Strata")
    import strata_tokenizer as stt

    from experiments.strata_specprefill.session_cases import build_sessions

    tdir = Path("/home/distilledoreo/AI/Strata-data/packs/swift-iq3_s/tokenizer")
    vocab = json.loads((tdir / "vocab.json").read_text())
    tarr = [None] * len(vocab)
    for k, v in vocab.items():
        tarr[v] = k
    ttok = stt.Tokenizer(tarr, (tdir / "merges.txt").read_text().split("\n"),
                         json.loads((tdir / "token_type.json").read_text()))
    def encode(s: str) -> list[int]:
        return ttok.encode(s, parse_special=True)

    built = build_sessions(args.seed)
    sets = []
    for ses in built["sessions"]:
        msgs, turns = ses["messages"], []
        for p in ses["probes"]:
            hist = msgs[:p["after_msg"] + 1]
            if p["q"] is not None:
                nxt = msgs[p["after_msg"] + 1] if p["after_msg"] + 1 < len(msgs) else None
                if nxt is not None and nxt["role"] == "assistant":
                    hist = hist + [nxt]
                hist = hist + [{"role": "user", "content": p["q"]}]
            ids = encode(render(hist))
            turns.append({"after_msg": p["after_msg"], "answer": p["answer"],
                          "evidence": p["evidence"], "kind": p.get("kind"),
                          "input_ids": ids})
        sets.append({"id": args.prefix + ses["id"].split("-", 1)[1],
                     "pattern": ses["pattern"], "turns": turns,
                     "seed": args.seed})
        print(json.dumps({"id": sets[-1]["id"], "turns": len(turns),
                          "tokens": [len(t["input_ids"]) for t in turns]}), flush=True)
    args.output.write_text(json.dumps(sets))
    print(f"saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
