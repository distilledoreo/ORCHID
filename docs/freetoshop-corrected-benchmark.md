# Corrected FreetoShop benchmark

The Linux launcher runs the sealed, contemporaneous Pi-native versus ORCHID
all-Solar comparison. It preserves the original starting repositories and
controller-private evaluator while creating a fresh artifact directory, stage,
sessions, and ORCHID database for every run.

## Local dependencies

The benchmark requires Python 3.11 or 3.12, Node.js, npm, and Pi Coding Agent
0.84.2. The prepared checkout uses:

```bash
.venv/bin/python
.benchmark-tools/node_modules/.bin/pi
```

Install them without making provider requests:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
npm install --prefix .benchmark-tools --ignore-scripts \
  @earendil-works/pi-coding-agent@0.84.2
```

## External inputs

Configure any OpenAI-compatible provider through one interactive command:

```bash
tools/start_freetoshop_corrected_all_solar.sh setup-provider
```

The prompt asks for the provider base URL, model ID, context window, token
prices, and API key. The base URL must end in `/v1`. The launcher stores the
configuration outside the repository at
`~/.config/orchid/benchmark.env`, readable only by the current user, and loads
it automatically for later commands. To use another location, set
`ORCHID_BENCHMARK_KEY_FILE`.

The endpoint must support OpenAI-compatible chat completions, streaming usage,
and structured JSON-schema responses; those are part of the sealed benchmark
contract.

Export the two controller-private paths in the launch shell:

```bash
export FREETOSHOP_HISTORICAL_STAGE='/path/to/freetoshop_pi_vs_orchid_20260820'
export FREETOSHOP_ORACLE_ROOT='/path/to/controller-private/FreetoShop'
```

The historical stage must contain `arm-a-pi`, `arm-b-orchid`, `pi-agent-a`,
and `pi-agent-b`. The two arm directories must retain their original Git
metadata. The oracle checkout must retain its Git metadata and
`tests/e2e/editor.spec.ts`.

If the original inputs are unavailable, they can be recovered from the retained
repository snapshot, RPC edit log, and hidden-evaluator hash:

```bash
.venv/bin/python tools/recover_freetoshop_benchmark_inputs.py \
  ../benchmark_recovered_inputs/freetoshop_20260820_recovered_v2
```

Recovery is accepted only when the reconstructed Git tree exactly equals the
original sealed tree hash. The Linux launcher automatically discovers the
default recovered directory above and records its recovery manifest in the new
benchmark manifest.

## Sealed workflow

Run the readiness check first:

```bash
tools/start_freetoshop_corrected_all_solar.sh check
```

The implementation and launcher must be committed before `prepare`; the freeze
step deliberately refuses a dirty tracked worktree. Preparation creates fresh
paths, freezes hashes, copies the sealed arms, and performs offline verification
without provider requests:

```bash
tools/start_freetoshop_corrected_all_solar.sh prepare
```

Paid provider work begins only with:

```bash
tools/start_freetoshop_corrected_all_solar.sh run
```

After the controller stops, collect build, quality, hidden-evaluator, token, and
memory-transition evidence with:

```bash
tools/start_freetoshop_corrected_all_solar.sh finalize
```

`all` performs the three phases sequentially. The launcher records the most
recent artifact and stage paths in the ignored `.benchmark-last-run.env`; it
never stores the provider credential there.
