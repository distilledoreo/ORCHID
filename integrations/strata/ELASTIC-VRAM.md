# Strata engine patch: `VRAM <reserve_mib> [drop]` for the elastic K/V

`elastic-vram.patch` (against Strata 0.1.43 `src/program/generate.cpp`) adds the
`VRAM` command to engines started with `--kv-grow`, whose expert cache is one VMM
range. A shrink frees the cache's spare chunks, the K/V past the live session,
then whole chunks of expert slots below the prompt loan (their experts become CPU
misses); a grow maps new chunks and refills them with the profile's hottest
experts. `drop` also discards the live conversation (its owner saved it or reads
it again). The SpecPrefill gateway uses it (`ORCHID_SPECPREFILL_ELASTIC_ENGINE`)
to lend the student 8 GB for lean turns and give it back for native agent turns.

Build (a separate copy; the installed Strata is not touched):

    cp -a Strata strata-elastic && cd strata-elastic
    patch -p1 < elastic-vram.patch
    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=60 \
      -DSTRATA_ENABLE_CUDA=ON -DSTRATA_EXPERIMENTAL_SM60=ON -DSTRATA_NATIVE_EXPERTS=ON
    ninja -C build strata

Measured on a P100 16 GB (Flash-Next IQ3_S, 256K window, `--prefill 1280`): 7.1 GB
freed in 0.41 s, refilled in 0.70 s; the live session and prompt cache survive.
Greedy answers can differ in wording after a resize (experts move between GPU and
CPU), as with any change of cache size.
