# Strata engine patch: `VRAM <reserve_mib> [drop]` for the elastic K/V

`elastic-vram.patch` (against Strata 0.1.43 `src/program/generate.cpp`) adds the
`VRAM` command to engines started with `--kv-grow`, whose expert cache is one VMM
range. A shrink frees the cache's spare chunks, the K/V past the live session,
then whole chunks of expert slots below the prompt loan (their experts become CPU
misses), and, when the student needs more, the lower part of the prompt loan with
the prompt chunk (8192 -> 512 for 8 GB); a grow maps new chunks, restores the loan
and its chunk, and refills them with the profile's hottest experts. `drop` also discards the live conversation (its owner saved it or reads
it again). The SpecPrefill gateway uses it (`ORCHID_SPECPREFILL_ELASTIC_ENGINE`)
to lend the student 8 GB for lean turns and give it back for native agent turns.

The patch also makes a deep prompt's K/V find room. The K/V grows into the expert
slots below the prompt loan, then into free VRAM. With the automatic 8192-token
chunk, the loan left a ~187K-token prompt too little of both: the K/V could not
grow, or it took the last VRAM and the verifier then failed to allocate. Now,
before a prompt's K/V grows, the engine checks the room past the reserve (at least
`--vram-reserve-mib`). When that room is short, the lower part of the loan goes
first and the prompt chunk shrinks (the same step `VRAM` takes). The next `VRAM`
grow restores it.

Build (a separate copy; the installed Strata is not touched):

    cp -a Strata strata-elastic && cd strata-elastic
    patch -p1 < elastic-vram.patch
    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=60 \
      -DSTRATA_ENABLE_CUDA=ON -DSTRATA_EXPERIMENTAL_SM60=ON -DSTRATA_NATIVE_EXPERTS=ON
    ninja -C build strata

Measured on a P100 16 GB (Flash-Next IQ3_S, 256K window, `--prefill auto`): 8.2 GB
freed in 0.43 s (the prompt chunk 8192 -> 512 meanwhile), given back in 0.79 s with
the chunk restored; prefill then runs at about 270 tokens/s, against 110 with a fixed
1,280-token chunk. The live session and prompt cache survive.
Greedy answers can differ in wording after a resize (experts move between GPU and
CPU), as with any change of cache size.
