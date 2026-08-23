# Tri-engine composite performance suite

Command: `npm run test:browser-perf:all`

| Engine   | Cases | Result | Notes                                |
| -------- | ----: | ------ | ------------------------------------ |
| Chromium |     5 | Pass   | Evidence JSON under this directory   |
| Firefox  |     5 | Pass   | Same suite; no engine-specific skips |
| WebKit   |     5 | Pass   | Same suite; no engine-specific skips |

Total: **15 passed**.

Suite covers: warm vs cold composite tile cache, worker cold path (128 + 256),
working-set budget, live canvas diagnostics.
