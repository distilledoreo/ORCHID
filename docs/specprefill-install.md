# SpecPrefill for Flash-Next: install, what changes, how to turn it off

**What it does.** The ORCHID gateway serves Flash-Next on the usual OpenAI API
(`/v1/chat/completions` and `/v1/responses`, so Codex, OpenCode and OpenAI SDKs
work unchanged). With SpecPrefill on, a small student model (Qwen3.5-0.8B) reads
long conversations and pasted documents, and Flash-Next reads only the parts the
current turn needs: on long conversations answers come about 2x faster than
native Flash-Next (1.6–2.2x by depth on a 256K real-session benchmark, with more
answers right). Tool-using agents (with the elastic engine below) run like native
Flash-Next, and a conversation can go on past Flash-Next's 256K window without the
client compacting it. Nothing changes in your model or its weights. Every
conversation is also kept losslessly on disk, so a thread resumes after a switch or
a restart without being read again.

## Install

You need the existing Flash-Next setup: a Strata checkout and its engine config
(the JSON `setup.py` wrote, naming `exe`, `args`, `cwd` and `tokenizer`), on an
NVIDIA GPU with 16 GB (measured on a Tesla P100). Then, from this repository:

    scripts/install-specprefill.sh --strata-config /path/to/strata-config.json

It creates a virtual environment (`~/.local/share/orchid/venv`), installs the
gateway with the student runtime (PyTorch 2.7.1, transformers 5.19.0; a few GB),
asks before downloading the student model (1.7 GB from Hugging Face, checked
against pinned checksums; nothing is downloaded without a yes), and writes the
settings to `~/.config/orchid/specprefill.env`. Options:

- `--student DIR`: a student model you already have (it is checked, not fetched).
- `--elastic`: also build Strata's elastic engine (a patched copy; your Strata is
  not touched) so agents run natively and the student borrows VRAM only when it
  helps. Needs the CUDA toolkit; about 15 minutes of compiling.

Start it, and check what it will do on your machine:

    ~/.local/share/orchid/venv/bin/orchid-specprefill serve      # http://127.0.0.1:18080/v1
    ~/.local/share/orchid/venv/bin/orchid-specprefill doctor

Settings you leave unset come from a measured table for your GPU. A GPU it has no
measurement for, or a missing student, runs native Flash-Next with a one-line
notice instead. Point clients at `http://127.0.0.1:18080/v1` with the model
`orchid-flash-next-specprefill`. For Codex, turn off its own compaction for this
model (`-c model_auto_compact_token_limit=1000000000000`): the gateway keeps the
whole history itself.

## Turn it off

One setting: `ORCHID_SPECPREFILL_ENGINE=native` (in the settings file or the
environment). The gateway then answers every request with plain Flash-Next through
Strata's own OpenAI path, at its full window; stored threads are kept for when
you turn SpecPrefill back on.

## Your stored conversations

Each thread's lossless archive lives under `~/.local/share/orchid/state`. To see,
export or delete them (a delete leaves nothing recoverable), or clear the caches:

    ~/.local/share/orchid/venv/bin/python -m memory_gateway.specprefill.admin list
    ~/.local/share/orchid/venv/bin/python -m memory_gateway.specprefill.admin export THREAD -o thread.jsonl
    ~/.local/share/orchid/venv/bin/python -m memory_gateway.specprefill.admin delete THREAD
    ~/.local/share/orchid/venv/bin/python -m memory_gateway.specprefill.admin purge-caches     # caches only; archives stay
    ~/.local/share/orchid/venv/bin/python -m memory_gateway.specprefill.admin storage

`ORCHID_SPECPREFILL_STATE_QUOTA_MIB` caps the archives (a warning at 80%, then
new turns go native until space is freed); below `MIN_FREE_DISK_MIB` free disk
the gateway answers natively instead of writing. Details of every setting:
[specprefill-gateway.md](specprefill-gateway.md).
