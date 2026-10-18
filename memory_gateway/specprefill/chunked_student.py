"""Chunked-prefill student trace: same scores without a full-length prefill.

P100 (no flash) OOMs on a 16k full prefill inside the model's own SDPA
(7.98 GiB ask). This module builds the KV cache in `prefill_block` chunks
with capture armed: if the backend passes past+current keys, the final block
directly yields row 0 (last prompt query) with no replay; otherwise tracing fails rather than replaying a token into a completed cache.

Long-context memory and speed (256K keys) on CUDA:

- Grouped-query attention runs without copying K/V to every query head
  (Transformers' SDPA path repeats them: ~1 GiB per tensor at 128K). Decode
  folds each head group into the query dimension; prefill attends to the
  cached past unmasked and to the current block causally, then merges both
  exactly with their log-sum-exp.
- Full-attention K/V grow in a preallocated buffer instead of re-concatenating
  every block.
- Lookahead drafts run on the live cache and roll back (recurrent states
  restored, K/V trimmed) instead of deep-copying the whole cache.
- Importance is reduced one layer at a time.
- Decode steps use plain matmuls (a few query rows against every key), and
  prefill attention over a long cached past runs as tiled online-softmax FP16
  GEMMs; PyTorch's memory-efficient kernel spent 95% of a 256K turn there.

Returns the same dict shape as `Student.trace` plus `scores`,
`replayed_last_token`, and `seen_key_lens` for equivalence checks (P2).
"""

from __future__ import annotations

import copy
import math
import time

from .core import importance, select_positions, token_hash

GROW_TOKENS = 16384  # K/V buffer growth step (tokens)


def last_query_logits(query, key, prompt_length):
    """Scaled q·K for the last query row against the first prompt_length keys.

    Grouped-query attention: query heads share key heads in contiguous groups.
    A grouped matmul gives the same result as repeat_interleave without
    materializing heads x keys copies (about 1 GiB per layer at 256K keys).
    Returns [heads, 1, prompt_length].
    """
    heads, kv_heads, dim = query.shape[1], key.shape[1], query.shape[-1]
    q = query[0, :, -1:].reshape(kv_heads, heads // kv_heads, dim)
    keys = key[0, :, :prompt_length]
    logits = q @ keys.transpose(-1, -2)  # [kv_heads, group, keys]
    return (logits / math.sqrt(dim)).reshape(heads, 1, prompt_length)


TILE_KEYS = 8192       # key tile for long-context attention over the cached past
LONG_PAST = 16384      # past length above which tiled matmul attention is used


def _tiled_past(torch, q, key, value, scale, tile=TILE_KEYS):
    """Online-softmax attention of folded queries over keys, via FP16 matmuls.

    q [b, kv, R, d] (unscaled); key/value [b, kv, n, d]. FlashAttention's
    recurrence in PyTorch with its mixed precision: scores and probabilities
    stay FP16 (exponents of non-positive numbers), the running max, sum and
    output accumulator are FP32. cuBLAS GEMMs run far closer to peak on Pascal
    than the generic memory-efficient kernel, and FP16 tiles avoid large
    conversions. Returns (out [b, kv, R, d] FP32, log-sum-exp [b, kv, R] FP32).
    """
    b, kv, rows, dim = q.shape
    qs = (q * scale).reshape(b * kv, rows, dim)
    k = key.reshape(b * kv, -1, dim)
    v = value.reshape(b * kv, -1, dim)
    top = torch.full((b * kv, rows), float('-inf'), device=q.device)
    total = torch.zeros((b * kv, rows), device=q.device)
    acc = torch.zeros((b * kv, rows, dim), device=q.device)
    for start in range(0, k.shape[1], tile):
        scores = torch.bmm(qs, k[:, start:start + tile].transpose(1, 2))
        new_top = torch.maximum(top, scores.amax(-1).float())
        keep = torch.exp(top - new_top)
        probs = torch.exp(scores - new_top.to(scores.dtype)[..., None])
        total = total * keep + probs.sum(-1, dtype=torch.float32)
        acc = acc * keep[..., None] + torch.bmm(probs, v[:, start:start + tile]).float()
        top = new_top
    out = (acc / total[..., None]).reshape(b, kv, rows, dim)
    return out, (top + torch.log(total)).reshape(b, kv, rows)


SPARSE_BLOCK = 64      # keys per summarized block for sparse prefill
SPARSE_LOCAL = 4096    # most recent past keys always attended densely
SPARSE_ROWS = 256      # query rows sharing one block selection
SPARSE_STEP = 1024     # query rows per bound/attention piece (memory vs GEMM size)


def _sparse_past(torch, q, key, value, scale, keep_keys, local=SPARSE_LOCAL,
                 block=SPARSE_BLOCK, rows_per_select=SPARSE_ROWS):
    """Query-aware block-sparse attention over the cached past (Quest-style).

    Each 64-key block is summarized by its elementwise key min and max, which
    upper-bound q·k for any key in it (q+ · max + q- · min). Every group of
    query rows keeps the `keep_keys` best-bounded blocks plus the most recent
    `local` keys, and attends to them exactly. Distant context that matters
    stays visible (unlike a sliding window); cost stops growing with history.
    q [1, kv, R, d] (unscaled); key/value [1, kv, n, d]. Returns
    (out [1, kv, R, d] FP32, log-sum-exp [1, kv, R] FP32).
    """
    _, kv, rows, dim = q.shape
    n = key.shape[2]
    far = max(0, (n - local) // block) * block
    k, v = key[0], value[0]
    blocks = k[:, :far].reshape(kv, far // block, block, dim)
    values = v[:, :far].reshape(kv, far // block, block, dim)
    kmax, kmin = blocks.amax(2), blocks.amin(2)
    near_k, near_v = k[:, far:], v[:, far:]
    keep = min(far // block, max(1, keep_keys // block))
    qs = (q[0] * scale).contiguous()
    out = torch.empty((kv, rows, dim), device=q.device)
    lse = torch.empty((kv, rows), device=q.device)
    # Blocks are chosen once per rows_per_select rows. Bounds and attention run
    # in SPARSE_STEP pieces: large enough for efficient GEMMs, small enough that
    # FP32 scores fit beside a co-located Flash-Next (~630 MiB at 256K).
    step = min(rows_per_select, SPARSE_STEP)
    for s0 in range(0, rows, rows_per_select):
        s1 = min(rows, s0 + rows_per_select)
        best = None
        for r0 in range(s0, s1, step):
            qq = qs[:, r0:min(s1, r0 + step)]
            bound = (torch.bmm(qq.clamp(min=0), kmax.transpose(1, 2))
                     + torch.bmm(qq.clamp(max=0), kmin.transpose(1, 2))).amax(1)
            best = bound if best is None else torch.maximum(best, bound)
        chosen = best.topk(keep, dim=-1).indices.sort(-1).values  # [kv, keep]
        index = chosen[..., None, None].expand(kv, keep, block, dim)
        sel_k = torch.cat([blocks.gather(1, index).reshape(kv, keep * block, dim), near_k], 1)
        sel_v = torch.cat([values.gather(1, index).reshape(kv, keep * block, dim), near_v], 1)
        for r0 in range(s0, s1, step):
            r1 = min(s1, r0 + step)
            scores = torch.bmm(qs[:, r0:r1], sel_k.transpose(1, 2)).float()
            part_lse = torch.logsumexp(scores, -1)
            probs = torch.exp(scores - part_lse[..., None]).to(sel_v.dtype)
            out[:, r0:r1] = torch.bmm(probs, sel_v).float()
            lse[:, r0:r1] = part_lse
    return out[None], lse[None]


def gqa_attention(torch, query, key, value, scaling, window=None, sparse_keys=None,
                  sparse_rows=SPARSE_ROWS):
    """Causal GQA attention for a query block at the end of key/value; no K/V copies.

    query [b, heads, q, d]; key/value [b, kv_heads, past + q, d]. With window, the
    block attends only to the last `window` past keys. Returns [b, heads, q, d].
    """
    b, heads, q_len, dim = query.shape
    kv_heads, kv_len = key.shape[1], key.shape[2]
    group, past = heads // kv_heads, kv_len - q_len
    query = query.contiguous()
    if q_len == 1:
        # Decode: a few query rows against every key is a matrix-vector pass.
        folded = query.reshape(b, kv_heads, group, dim)
        scores = (torch.matmul(folded * scaling, key.transpose(-1, -2))).float().softmax(-1)
        out = torch.matmul(scores.to(value.dtype), value)
        return out.reshape(b, heads, 1, dim)
    efficient = torch.ops.aten._scaled_dot_product_efficient_attention
    # Current block: causal, only its own (small) keys are expanded.
    own_k = key[:, :, past:].repeat_interleave(group, dim=1)
    own_v = value[:, :, past:].repeat_interleave(group, dim=1)
    out_c, lse_c, *_ = efficient(query, own_k, own_v, None, True, 0.0, True, scale=scaling)
    start = max(0, past - window) if window else 0
    if past <= start:
        return out_c
    # Past keys: every query sees all of them, so no mask; heads fold into rows.
    folded = query.reshape(b, kv_heads, group * q_len, dim)
    past_k, past_v = key[:, :, start:past], value[:, :, start:past]
    if sparse_keys and past - start > 2 * (sparse_keys + SPARSE_LOCAL):
        out_p, lse_p = _sparse_past(torch, folded, past_k, past_v, scaling, sparse_keys,
                                    rows_per_select=sparse_rows)
    elif past - start > LONG_PAST:
        out_p, lse_p = _tiled_past(torch, folded, past_k, past_v, scaling)
    else:
        out_p, lse_p, *_ = efficient(folded, past_k, past_v, None, True, 0.0, False, scale=scaling)
        lse_p = lse_p[..., :group * q_len]
    out_p = out_p.reshape(b, heads, q_len, dim)
    lse_p = lse_p.reshape(b, heads, q_len)
    lse_c = lse_c[..., :q_len]
    top = torch.maximum(lse_p, lse_c)
    w_p, w_c = torch.exp(lse_p - top)[..., None], torch.exp(lse_c - top)[..., None]
    return ((out_p.float() * w_p + out_c.float() * w_c) / (w_p + w_c)).to(query.dtype)


def growable_layer_class():
    """DynamicLayer whose K/V live in a buffer grown in GROW_TOKENS steps."""
    from transformers.cache_utils import DynamicLayer

    class GrowableLayer(DynamicLayer):
        def _adopt(self):
            n = self.keys.shape[-2] if self.keys is not None and self.keys.numel() else 0
            self._buf_k, self._buf_v, self._len = self.keys, self.values, n

        def _reserve(self, need, like_k, like_v):
            capacity = self._buf_k.shape[-2] if self._buf_k is not None and self._buf_k.numel() else 0
            if need <= capacity:
                return
            size = -(-need // GROW_TOKENS) * GROW_TOKENS
            shape_k = (*like_k.shape[:-2], size, like_k.shape[-1])
            shape_v = (*like_v.shape[:-2], size, like_v.shape[-1])
            new_k = like_k.new_empty(shape_k)
            new_v = like_v.new_empty(shape_v)
            if self._len:
                new_k[..., :self._len, :] = self._buf_k[..., :self._len, :]
                new_v[..., :self._len, :] = self._buf_v[..., :self._len, :]
            self._buf_k, self._buf_v = new_k, new_v

        def update(self, key_states, value_states, *args, **kwargs):
            if not self.is_initialized:
                self.lazy_initialization(key_states, value_states)
            if not hasattr(self, '_len'):
                self._adopt()
            n = key_states.shape[-2]
            self._reserve(self._len + n, key_states, value_states)
            self._buf_k[..., self._len:self._len + n, :] = key_states
            self._buf_v[..., self._len:self._len + n, :] = value_states
            self._len += n
            self.keys = self._buf_k[..., :self._len, :]
            self.values = self._buf_v[..., :self._len, :]
            return self.keys, self.values

        def crop(self, tokens_to_remove):
            if not hasattr(self, '_len'):
                return super().crop(tokens_to_remove)
            self._len = max(0, self._len - abs(tokens_to_remove))
            self.keys = self._buf_k[..., :self._len, :]
            self.values = self._buf_v[..., :self._len, :]

        def reset(self):
            for name in ('_buf_k', '_buf_v', '_len'):
                self.__dict__.pop(name, None)
            super().reset()

    return GrowableLayer


_GROWABLE = None


def make_growable(cache):
    """Switch a cache's plain full-attention layers to buffered growth (in place)."""
    global _GROWABLE
    from transformers.cache_utils import DynamicLayer
    if _GROWABLE is None:
        _GROWABLE = growable_layer_class()
    for layer in getattr(cache, 'layers', ()):
        if type(layer) is DynamicLayer:
            layer.__class__ = _GROWABLE


def snapshot(cache):
    """Recurrent/conv states (small) and full-attention lengths, for rollback."""
    saved = []
    for layer in cache.layers:
        state = {}
        for name in ('conv_states', 'recurrent_states'):
            states = getattr(layer, name, None)
            if isinstance(states, dict):
                state[name] = {i: t.clone() for i, t in states.items() if t is not None}
        if getattr(layer, 'keys', None) is not None and not isinstance(getattr(layer, 'conv_states', None), dict):
            state['length'] = layer.get_seq_length()
        saved.append(state)
    return saved


def rollback(cache, saved):
    for layer, state in zip(cache.layers, saved):
        for name in ('conv_states', 'recurrent_states'):
            for i, tensor in state.get(name, {}).items():
                getattr(layer, name)[i].copy_(tensor)
        if 'length' in state:
            extra = layer.get_seq_length() - state['length']
            if extra > 0:
                layer.crop(extra)


def streaming_importance(torch, logits, pool_kernel=13):
    """core.importance computed one layer at a time (identical result).

    importance = mean over rows of the max over (layer, head) of pooled softmax;
    max is associative, so taking it layer by layer keeps memory per layer.
    """
    if logits.ndim != 4 or pool_kernel < 1 or pool_kernel % 2 == 0:
        raise ValueError("Expected four-dimensional logits and an odd pooling kernel")
    best = None
    for layer in logits:
        attn = layer.softmax(-1, dtype=torch.float32).to(logits.dtype)
        attn = torch.nn.functional.avg_pool1d(attn, pool_kernel, stride=1, padding=pool_kernel // 2)
        layer_max = attn.max(0).values
        best = layer_max if best is None else torch.maximum(best, layer_max)
    return best.mean(0)


def trace_chunked(model, torch, stop_ids, ids, keep_ratio, prefill_block=8192,
                  lookahead=8, pool_kernel=13, chunk_size=32, device="cuda", prefix_state=None,
                  prefill_window=None, sparse_keys=None, sparse_rows=SPARSE_ROWS):
    """prefill_window: if set, full-attention layers attend only to the most recent
    prefill_window keys (plus the block itself) while prefilling. The last prompt
    query and the lookahead rows still score against every key, and the linear
    attention layers keep their full recurrent state. Cost per turn then stays
    bounded as history grows.

    sparse_keys: if set (CUDA only), prefill attention over a long cached past
    keeps only the sparse_keys best blocks per query group plus a local window
    (_sparse_past). Scoring rows and lookahead stay dense over every key.
    sparse_rows: folded query rows (head-major, so rows of one head) that share
    one block selection."""
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    if lookahead < 1:
        raise ValueError("lookahead counts query rows and must be positive")
    if prefill_block < 1:
        raise ValueError("prefill_block must be positive")
    original = ALL_ATTENTION_FUNCTIONS["sdpa"]
    long_context = device == "cuda"
    current = []
    rows, layers, sampled = [], [], []
    seen_key_lens = []
    prompt_length = len(ids)

    def record(module, query, key):
        current.append((module.layer_idx, last_query_logits(query, key, prompt_length)))

    def attend(module, query, key, value, attention_mask, scaling, window, kwargs, sparse=None):
        if long_context:
            scale = scaling if scaling is not None else 1 / math.sqrt(query.shape[-1])
            out = gqa_attention(torch, query, key, value, scale, window, sparse, sparse_rows)
            return out.transpose(1, 2).contiguous(), None
        q_len, kv_len = int(query.shape[2]), int(key.shape[2])
        if window and kv_len > window + q_len:
            keep = window + q_len
            key, value = key[:, :, -keep:], value[:, :, -keep:]
            if attention_mask is not None:
                attention_mask = attention_mask[..., -keep:]
            else:
                rows_ = torch.arange(q_len, device=query.device)[:, None]
                cols = torch.arange(keep, device=query.device)[None, :]
                attention_mask = (cols <= rows_ + (keep - q_len))[None, None]
            kwargs.pop("is_causal", None)
        return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

    def capture_prefill(module, query, key, value, attention_mask, scaling=None, **kwargs):
        seen_key_lens.append(int(key.shape[2]))
        # Final block only: backend passes past+current keys covering the prompt.
        if int(key.shape[2]) == prompt_length and int(query.shape[2]) >= 1:
            record(module, query, key)  # Scoring always sees every key.
        return attend(module, query, key, value, attention_mask, scaling, prefill_window, kwargs,
                      sparse_keys)

    def capture_decode(module, query, key, value, attention_mask, scaling=None, **kwargs):
        # Generated lookahead query attends to the unchanged prompt keys.
        record(module, query, key)
        return attend(module, query, key, value, attention_mask, scaling, None, kwargs)

    if device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    reused = 0
    past = None
    if prefix_state is not None:
        previous = prefix_state.get("ids", [])
        if previous and len(previous) < len(ids) and ids[:len(previous)] == previous:
            reused = len(previous)
            past = prefix_state["cache"]
        prefix_state.clear()  # Fail closed if any model/capture operation fails.
    ALL_ATTENTION_FUNCTIONS.register("sdpa", capture_prefill)
    try:
        with torch.inference_mode():
            blocks = [ids[i:i + prefill_block] for i in range(reused, len(ids), prefill_block)]
            output = None
            for b in blocks:
                inputs = torch.tensor([b], dtype=torch.long, device=device)
                output = model(
                    input_ids=inputs,
                    past_key_values=output.past_key_values if output is not None else past,
                    use_cache=True,
                    logits_to_keep=1,
                )
                if long_context:
                    make_growable(output.past_key_values)
            # Lookahead/decodes use the unconditional capture from here on.
            ALL_ATTENTION_FUNCTIONS.register("sdpa", capture_decode)
            replayed = False
            if not current:
                raise RuntimeError("Backend did not capture the last prompt query; token replay is unsafe")
            cache, saved = output.past_key_values, None
            if prefix_state is not None:
                if long_context:
                    # Drafts mutate the live cache; roll back afterwards (no full copy).
                    saved = snapshot(cache)
                else:
                    # Hybrid recurrent state cannot be cropped after speculative lookahead.
                    # Keep authoritative prompt state, and mutate a separate branch for drafts.
                    prefix_state.update(ids=list(ids), cache=cache)
                    output.past_key_values = copy.deepcopy(cache)
            for _ in range(lookahead):
                layer_ids = [i for i, _ in current]
                if not current or (layers and layer_ids != layers):
                    raise RuntimeError("Missing or inconsistent full-attention Q/K capture")
                layers = layer_ids
                rows.append(torch.stack([lg for _, lg in current]))
                current.clear()
                token = output.logits[:, -1:].argmax(-1)
                sampled.append(token.item())
                if len(sampled) >= lookahead or sampled[-1] in stop_ids:
                    break
                output = model(
                    input_ids=token,
                    past_key_values=output.past_key_values,
                    use_cache=True,
                    logits_to_keep=1,
                )
            if saved is not None:
                rollback(cache, saved)
                prefix_state.update(ids=list(ids), cache=cache)
            logits = torch.cat(rows, dim=2)
            scores = (streaming_importance(torch, logits, pool_kernel) if long_context
                      else importance(logits, pool_kernel))
            positions = select_positions(scores, keep_ratio, chunk_size)
            if device == "cuda":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            return {
                "positions": positions,
                "input_hash": token_hash(ids),
                "input_tokens": len(ids),
                "query_rows": len(rows),
                "attention_layer_ids": layers,
                "draft_sampled_ids": sampled,
                "student_selection_s": elapsed,
                "peak_student_allocator_mib": torch.cuda.max_memory_allocated() / 2**20 if device == "cuda" else None,
                "score_hash": token_hash(scores.float().tolist()),
                "scores": scores.float().tolist(),
                "prefill_block": prefill_block,
                "reused_prefix_tokens": reused,
                "prefill_tokens": len(ids) - reused,
                "method": "chunked-prefill",
                "replayed_last_token": replayed,
                "seen_key_lens": sorted(set(seen_key_lens)),
            }
    except BaseException:
        if prefix_state is not None:
            prefix_state.clear()
        raise
    finally:
        ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
