"""Opt-in, single-owner SpecPrefill session with addressable compaction.

No service defaults change. All model calls are explicit injected adapters. Raw
sources live independently of both models' current cache generations.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import heapq
import json
import math
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .archive_manifests import ArchiveManifests
from .archive_search import ArchiveSearch
from .core import token_hash
from .exact_lookup import ArchiveSources, LookupUnavailable, exact_block, references
from .omission_archive import OmissionArchive
from .paged_archive import PagedArchive


@dataclass(frozen=True)
class SummaryEntry:
    text: str
    references: tuple[str, ...]


@dataclass(frozen=True)
class Limits:
    student_context: int = 8192
    target_context: int = 8192
    compact_tokens: int = 3072
    expansion_tokens: int = 2048
    answer_tokens: int = 128
    summary_tokens: int = 1024
    max_candidates: int = 64

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError('All limits must be positive integers')
        if self.compact_tokens + self.answer_tokens >= min(self.student_context, self.target_context):
            raise ValueError('Compaction must leave room for the next turn')


class CompactingSession:
    """Append between explicit compactions; rebuild both models at a boundary.

    selector(ids) -> {positions: increasing token indices, scores: finite floats}
    target(ids, maximum) -> generated token IDs, including any stop token
    summarize(request_dict) -> sequence[SummaryEntry]

    The summarizer sees the bounded current target transcript, previous summaries,
    and newly introduced cues, never a hidden ground-truth answer. References must
    cover every original archive chunk, directly or through previous entry IDs.
    Each instance owns its student cache and a serialized target conversation.
    References are compact external manifests; they do not grow inline source lists.
    """

    def __init__(self, tokenizer, selector, target, summarize, *, system, limits=None,
                 checkpoint=None, retrieval_mode='indexed', embedder=None, embedding_id=None,
                 archive_cache_bytes=8 * 1024**2, exact_lookup=True, _storage=None, _search_path=None):
        self.tok, self.selector, self.target, self.summarize = tokenizer, selector, target, summarize
        self.limits = limits or Limits()
        self.system = system
        if type(exact_lookup) is not bool:
            raise ValueError('exact_lookup must be boolean')
        self.exact_lookup_enabled = exact_lookup
        if retrieval_mode not in ('bounded', 'scan', 'indexed', 'indexed-scan'):
            raise ValueError('Unknown retrieval mode')
        self.retrieval_mode = retrieval_mode
        self.checkpoint = Path(checkpoint) if checkpoint else None
        if self.checkpoint and self.checkpoint.exists():
            raise FileExistsError('Use restore() to reopen an existing session')
        self.archive = _storage or (PagedArchive(self.checkpoint.with_suffix('.archive.sqlite3'), tokenizer,
            cache_bytes=archive_cache_bytes) if self.checkpoint else OmissionArchive(tokenizer))
        if self.checkpoint and isinstance(self.archive, PagedArchive) and self.archive.load_session() is not None:
            self.archive.close()
            raise FileExistsError('An existing paged archive must be reopened with restore()')
        self.index = ArchiveManifests(self.archive)
        self.source_cues = self.archive.cues if isinstance(self.archive, PagedArchive) else {}
        search_path = _search_path or (self.checkpoint.with_suffix('.search.sqlite3') if self.checkpoint else None)
        if search_path:
            search_path.parent.mkdir(parents=True, exist_ok=True)
        self.search = ArchiveSearch(self.archive, path=search_path,
                                    embedder=embedder, embedding_id=embedding_id)
        self.search.sync(self.source_cues)
        self.entries = []
        self.generation = 0
        self.revision = 0
        self.next_original = 0
        self.turn = 0
        self.last_answer = None
        self.events = self.archive.session_events if isinstance(self.archive, PagedArchive) else []
        self.lock = threading.RLock()
        self.student_ids = self._system()
        self.target_ids = self._system()
        self.student_spans = {}
        self.target_spans = {}
        self.summary_spans = []
        self.cache_confirmed = False
        self._fits(self.student_ids, student=True)
        self._fits(self.target_ids, student=False)
        self._save()

    def _encode(self, text, special=False):
        return list(self.tok.encode(text, parse_special=special))

    def _frame(self, role, text):
        # Source strings never parse special tokens or acquire a system role.
        return (self._encode(f'<|im_start|>{role}\n', True) + self._encode(text)
                + self._encode('<|im_end|>\n', True))

    def _system(self):
        return self._frame('system', self.system)

    def _query(self, question):
        return self._frame('user', question) + self._encode(
            '<|im_start|>assistant\n<think>\n\n</think>\n\n', True)

    def _reply(self, ids):
        # The native adapter decodes special tokens and re-encodes them exactly.
        stop = set(getattr(self.target, 'stop_ids', (248044, 248046)))
        suffix = [] if ids and ids[-1] in stop else self._encode('<|im_end|>', True)
        return list(ids) + suffix + self._encode('\n')

    def _fits(self, ids, *, student, reserve=None):
        maximum = self.limits.student_context if student else self.limits.target_context
        reserve = self.limits.answer_tokens if reserve is None else reserve
        if len(ids) + reserve > maximum:
            raise ValueError(f'{"Student" if student else "Target"} context budget exceeded; compact before appending')

    def _trace(self, ids):
        self._fits(ids, student=True)
        result = self.selector(list(ids))
        scores, positions = result['scores'], result['positions']
        if (len(scores) != len(ids) or any(not math.isfinite(float(s)) or s < 0 for s in scores)
                or any(type(p) is not int or not 0 <= p < len(ids) for p in positions)
                or any(a >= b for a, b in zip(positions, positions[1:]))):
            raise ValueError('Invalid student trace')
        return result

    @staticmethod
    def _hits(spans, trace):
        selected = set(trace['positions'])
        hits = [h for h, (a, b) in spans.items() if any(p in selected for p in range(a, b))]
        # Rank whole exact archive chunks; original source boundaries stay intact.
        return sorted(hits, key=lambda h: (-sum(trace['scores'][spans[h][0]:spans[h][1]])
                                          / (spans[h][1] - spans[h][0]), h))

    def _evidence(self, handles, present=()):
        text, placements = self.archive.evidence(handles, present=present)
        prefix = self._encode('<|im_start|>user\n', True)
        content = self._encode(text)
        if not text:
            return [], {}
        offsets = [0]
        for t in content:
            offsets.append(offsets[-1] + len(self.tok.token_bytes(t)))
        spans = {}
        for h, a, b in placements:
            byte_a, byte_b = len(text[:a].encode()), len(text[:b].encode())
            spans[h] = (len(prefix) + bisect.bisect_right(offsets, byte_a) - 1,
                        len(prefix) + bisect.bisect_left(offsets, byte_b))
        return prefix + content + self._encode('<|im_end|>\n', True), spans

    @staticmethod
    def _extend(ids, spans, new_ids, new_spans):
        at = len(ids)
        ids.extend(new_ids)
        spans.update({h: (a + at, b + at) for h, (a, b) in new_spans.items()})

    def _archive(self, path, version, text):
        sid = self.archive.add(path, version, text, self.next_original, self.turn)
        self.next_original += len(self.archive.sources[sid]['token_ids'])
        return sid

    def close(self):
        with self.lock:
            self.search.close()
            if isinstance(self.archive, PagedArchive):
                self.archive.close()

    def _text_spans(self, sid, ids, offset=0):
        """Cover UTF-8 source bytes even when generated token boundaries differ."""
        offsets = [0]
        for t in ids:
            offsets.append(offsets[-1] + len(self.tok.token_bytes(t)))
        return {h: (offset + bisect.bisect_right(offsets, self.archive.chunks[h].byte_start) - 1,
                    offset + bisect.bisect_left(offsets, self.archive.chunks[h].byte_end))
                for h in self.archive.resolve(sid)}

    def add_source(self, path, version, text, *, cue):
        """Persist raw input before any omission. Cue is a readable retrieval hint."""
        with self.lock:
            if (type(version) is not int or version < 1 or not isinstance(path, str)
                    or not path or not isinstance(text, str) or not text):
                raise ValueError('Invalid source provenance')
            if not isinstance(cue, str) or not cue.strip():
                raise ValueError('A source needs a nonempty retrieval cue')
            known = self.archive.source_id(path, version)
            # Admission fails before changing either active prompt.
            sid = known or f'S{len(self.archive.sources) + 1}'
            if known and self.archive.sources[known]['text'] != text:
                raise ValueError('An immutable source version cannot change content')
            if known:
                self.search.add_source(sid, self.source_cues.get(sid, cue))
                return sid
            old_original = self.next_original
            if isinstance(self.archive, PagedArchive):
                try:
                    with self.archive.savepoint():
                        sid = self._archive(path, version, text)
                        new_ids, spans = self._evidence(self.archive.resolve(sid))
                        self._fits(self.student_ids + new_ids, student=True)
                        self.search.add_source(sid, cue)
                except BaseException:
                    self.next_original = old_original
                    raise
            else:
                old_archive = copy.deepcopy(self.archive.to_dict())
                sid = self._archive(path, version, text)
                new_ids, spans = self._evidence(self.archive.resolve(sid))
                try:
                    self._fits(self.student_ids + new_ids, student=True)
                    self.search.add_source(sid, cue)
                except Exception:
                    self.archive = OmissionArchive.from_dict(old_archive, self.tok)
                    self.index.archive = self.archive
                    self.search.archive = self.archive
                    self.next_original = old_original
                    raise
            self.entries.append(SummaryEntry(f'{path} v{version}: {cue}', (sid,)))
            self.source_cues[sid] = cue
            self._extend(self.student_ids, self.student_spans, new_ids, spans)
            self.revision += 1
            self._save()
            return sid

    def _bounded(self, handles, *, prefix, present, suffix, maximum, token_budget, count, atomic_groups=()):
        groups = {}
        for block in atomic_groups:
            block = frozenset(block)
            for h in block:
                groups[h] = block
        chosen, skipped = [], set()
        for h in handles:
            if len(chosen) >= count:
                break
            if h in present or h in chosen or h in skipped:
                continue
            block = [x for x in groups.get(h, (h,)) if x not in present and x not in chosen]
            if not block:
                continue
            if len(chosen) + len(block) > count:
                skipped.update(block)
                continue
            trial, _ = self._evidence(chosen + block)
            if len(trial) <= token_budget and len(prefix) + len(trial) + len(suffix) <= maximum:
                chosen.extend(block)
            else:
                # Never admit half a definition unit; later siblings skip too.
                skipped.update(block)
        return chosen

    def _scan(self, handles, query):
        """Visit every nominated chunk once in finite windows, retain a shortlist.

        Cross-window attention magnitudes are a heuristic ranking, not a proof of
        equivalence to joint full-context attention. Final candidates are rescored
        together in the ordinary working context.
        """
        best, batch, passes, scanned = [], [], 0, 0
        prefix = self._system()
        budget = min(self.limits.expansion_tokens,
                     self.limits.student_context - self.limits.answer_tokens - len(prefix) - len(query))

        def score_batch():
            nonlocal passes, scanned
            evidence, spans = self._evidence(batch)
            ids, mapped = list(prefix), {}
            self._extend(ids, mapped, evidence, spans)
            trace = self._trace(ids + query)
            passes += 1
            scanned += len(batch)
            for h in self._hits(mapped, trace):
                a, b = mapped[h]
                score = sum(trace['scores'][a:b]) / (b - a)
                heapq.heappush(best, (score, h))
                if len(best) > self.limits.max_candidates:
                    heapq.heappop(best)

        for h in handles:
            trial, _ = self._evidence(batch + [h])
            if batch and (len(trial) > budget or len(batch) >= self.limits.max_candidates):
                score_batch()
                batch = []
            single, _ = self._evidence([h])
            if len(single) > budget:
                raise ValueError('An exact archive chunk cannot fit the scan window')
            batch.append(h)
        if batch:
            score_batch()
        return [h for _, h in sorted(best, reverse=True)], passes, scanned

    def _exact_lookup(self, question, query):
        """Reserve one complete immutable evidence block for both model inputs."""
        if not self.exact_lookup_enabled:
            return [], {'status': 'disabled'}
        if not references(question):
            return [], {'status': 'not_applicable'}
        try:
            text, units = exact_block(question, ArchiveSources(self.archive), self._encode,
                                      budget=self.limits.expansion_tokens)
            frame = self._frame('user', text)
            if len(frame) > self.limits.expansion_tokens:
                raise LookupUnavailable('Framed exact evidence exceeds expansion budget')
            if (len(self.student_ids) + len(frame) + len(query) + self.limits.answer_tokens
                    > self.limits.student_context or
                    len(self.target_ids) + len(frame) + len(query) + self.limits.answer_tokens
                    > self.limits.target_context):
                raise LookupUnavailable('Exact evidence needs more context headroom')
        except LookupUnavailable as error:
            return [], {'status': 'deferred', 'reason': str(error)}
        return frame, {'status': 'delivered', 'tokens': len(frame),
                       'units': [{k: v for k, v in u.items() if k not in ('text', 'route_text')}
                                 for u in units]}

    def answer(self, question, *, maximum=None):
        """One summary nomination, at most one expansion/rescore, then target decode."""
        with self.lock:
            maximum = self.limits.answer_tokens if maximum is None else maximum
            if type(maximum) is not int or not 1 <= maximum <= self.limits.answer_tokens:
                raise ValueError('Requested output exceeds configured session limit')
            if not isinstance(question, str) or not question.strip():
                raise ValueError('Question must be nonempty')
            query = self._query(question)
            exact, lookup = self._exact_lookup(question, query)
            suffix = exact + query
            expansion_budget = self.limits.expansion_tokens - len(exact)
            probe = self.student_ids + suffix
            trace = self._trace(probe)
            candidates = []
            scopes = []
            selected = set(trace['positions'])
            nominated = [(i, a, b) for i, (a, b) in enumerate(self.summary_spans)
                         if any(p in selected for p in range(a, b))]
            nominated.sort(key=lambda item: -sum(trace['scores'][item[1]:item[2]])
                           / (item[2] - item[1]))
            for i, _, _ in nominated:
                for ref in self.entries[i].references:
                    scopes.append(ref)
                    if not isinstance(self.archive, PagedArchive):
                        candidates.extend(self.index.chunks(ref))
            retrieval, atomic = None, ()
            if self.retrieval_mode.startswith('indexed'):
                # Search all archive handles only if nomination supplies no scope,
                # or a nominated scope has no search hits. Neither path reads it
                # through the student. Scope widening is at most one extra query.
                initial_scope = (self.archive.select_handles(scopes, exclude=self.student_spans)
                    if isinstance(self.archive, PagedArchive) else (candidates if scopes else self.archive.chunks))
                # Explicit path/version/symbol mentions pin complete definition
                # units ahead of fuzzy ranking; fuzzy hits can no longer displace
                # them, and narrowed candidacy fits the expansion budget.
                pinned = self.search.bind_references(question)
                hits = self.search.search(question, initial_scope, limit=self.limits.max_candidates,
                                          exclude=self.student_spans, pinned=pinned)
                widened = bool(not scopes)
                first_scope_chunks = self.search.last['scope_chunks']
                if not hits and scopes:
                    hits = self.search.search(question, self.archive.chunks,
                        limit=self.limits.max_candidates, exclude=self.student_spans, pinned=pinned)
                    widened = True
                retrieval = {**self.search.last, 'widened': widened,
                             'initial_scope_chunks': first_scope_chunks}
                candidates = [hit.handle for hit in hits]
                atomic = self.search.unit_blocks(candidates)
            if isinstance(self.archive, PagedArchive) and not self.retrieval_mode.startswith('indexed'):
                available = self.archive.select_handles(scopes, exclude=self.student_spans)
                candidates = available
            else:
                available = list(dict.fromkeys(h for h in candidates if h not in self.student_spans))
            candidates = self._bounded(candidates, prefix=self.student_ids,
                present=self.student_spans, suffix=suffix,
                maximum=self.limits.student_context - self.limits.answer_tokens,
                token_budget=expansion_budget, count=self.limits.max_candidates,
                atomic_groups=atomic if retrieval is not None else ())
            scan_passes = scan_chunks = 0
            if self.retrieval_mode == 'indexed-scan' and not available:
                available = (self.archive.select_handles(exclude=self.student_spans)
                    if isinstance(self.archive, PagedArchive) else
                    [h for h in self.archive.chunks if h not in self.student_spans])
                ranked, scan_passes, scan_chunks = self._scan(available, query)
                candidates = self._bounded(ranked, prefix=self.student_ids,
                    present=self.student_spans, suffix=suffix,
                    maximum=self.limits.student_context - self.limits.answer_tokens,
                    token_budget=expansion_budget, count=self.limits.max_candidates)
            if self.retrieval_mode == 'scan' and len(candidates) < len(available):
                ranked, scan_passes, scan_chunks = self._scan(available, query)
                candidates = self._bounded(ranked, prefix=self.student_ids,
                    present=self.student_spans, suffix=suffix,
                    maximum=self.limits.student_context - self.limits.answer_tokens,
                    token_budget=expansion_budget, count=self.limits.max_candidates)
            expanded, spans = self._evidence(candidates)
            student_ids, student_spans = list(self.student_ids), dict(self.student_spans)
            self._extend(student_ids, student_spans, expanded, spans)
            student_ids.extend(exact)
            student_prompt = student_ids + query
            if candidates:
                trace = self._trace(student_prompt)
            handles = self._hits(student_spans, trace)
            handles = self._bounded(handles, prefix=self.target_ids, present=self.target_spans,
                suffix=suffix, maximum=self.limits.target_context - self.limits.answer_tokens,
                token_budget=expansion_budget, count=self.limits.max_candidates)
            evidence, placements = self._evidence(handles)
            target_ids, target_spans = list(self.target_ids), dict(self.target_spans)
            self._extend(target_ids, target_spans, evidence, placements)
            target_ids.extend(exact)
            target_prompt = target_ids + query
            self._fits(target_prompt, student=False)
            # Durable question before invoking the target; a failed call can be retried.
            qsid = self._archive(f'conversation/{self.turn}/user', 1, question)
            question_chunks = set(self.archive.resolve(qsid))
            if not any((self.archive.references_contain(e.references, question_chunks)
                        if isinstance(self.archive, PagedArchive) else
                        question_chunks <= {h for ref in e.references for h in self.index.chunks(ref)})
                       for e in self.entries):
                self.entries.append(SummaryEntry(f'User: {question[:256]}', (qsid,)))
            self.source_cues[qsid] = question
            self._save()
            self.search.add_source(qsid, question)
            try:
                output = list(self.target(target_prompt, maximum))
                if not output or len(output) > maximum:
                    raise ValueError('Invalid target output length')
                decoded = b''.join(self.tok.token_bytes(t) for t in output).decode('utf-8')
                reply = self._reply(output)
                self._fits(student_prompt + reply, student=True, reserve=0)
                self._fits(target_prompt + reply, student=False, reserve=0)
            except BaseException:
                self.cache_confirmed = False
                if hasattr(self.selector, 'reset'):
                    self.selector.reset()
                raise
            rsid = self._archive(f'conversation/{self.turn}/assistant', 1, decoded)
            self.source_cues[rsid] = decoded[:256]
            self.entries.append(SummaryEntry(f'Assistant: {decoded[:256]}', (rsid,)))
            question_ids = self._encode(question)
            role_length = len(self._encode('<|im_start|>user\n', True))
            for base, spans in ((len(student_ids), student_spans), (len(target_ids), target_spans)):
                spans.update(self._text_spans(qsid, question_ids, base + role_length))
            student_spans.update(self._text_spans(rsid, output, len(student_prompt)))
            target_spans.update(self._text_spans(rsid, output, len(target_prompt)))
            self.student_ids = student_prompt + reply
            self.target_ids = target_prompt + reply
            self.student_spans, self.target_spans = student_spans, target_spans
            self.turn += 1
            self.last_answer = {'question': question, 'output_ids': list(output), 'turn': self.turn,
                                'maximum': maximum}
            self.revision += 1
            self.cache_confirmed = True
            self.events.append({'kind': 'answer', 'generation': self.generation,
                                'expanded': candidates, 'delivered': handles,
                                'expansion_candidates': len(available),
                                'expansion_deferred': len(available) - len(candidates),
                                'scan_passes': scan_passes, 'scan_chunks': scan_chunks,
                                'retrieval': retrieval,
                                'exact_lookup': lookup,
                                'student_tokens': len(student_prompt), 'target_tokens': len(target_prompt)})
            self._save()
            self.search.add_source(rsid, self.source_cues[rsid])
            return output

    def _normalize_entries(self, entries, *, make_manifest=True):
        old = {f'M{i}': e.references for i, e in enumerate(self.entries)}
        result = []
        for entry in entries:
            if (not isinstance(entry, SummaryEntry) or not isinstance(entry.text, str)
                    or not entry.text.strip() or not entry.references
                    or any(not isinstance(ref, str) for ref in entry.references)):
                raise ValueError('Each summary item needs text and provenance')
            refs = []
            for ref in entry.references:
                for source_ref in old.get(ref, (ref,)):
                    if isinstance(self.archive, PagedArchive):
                        self.archive.validate_reference(source_ref)
                    else:
                        list(self.index.chunks(source_ref))  # Never resolves to summary text.
                    refs.append(source_ref)
            references = (self.index.intern(refs),) if make_manifest else tuple(dict.fromkeys(refs))
            result.append(SummaryEntry(entry.text, references))
        if isinstance(self.archive, PagedArchive):
            covered = self.archive.covers(ref for e in result for ref in e.references)
        else:
            coverage = {h for e in result for ref in e.references for h in self.index.chunks(ref)}
            covered = coverage == set(self.archive.chunks)
        if not covered:
            raise ValueError('Compaction would orphan archive references')
        return result

    def _seed(self, entries):
        ids, spans = self._system(), []
        ids.extend(self._encode('<|im_start|>user\n', True))
        ids.extend(self._encode('Working memory. Summary items are cues; references resolve to exact archived sources.\n'))
        for i, entry in enumerate(entries):
            start = len(ids)
            ids.extend(self._encode(f'[M{i} refs={",".join(entry.references)}] {entry.text}\n'))
            spans.append((start, len(ids)))
        ids.extend(self._encode('<|im_end|>\n', True))
        if len(ids) - len(self._system()) > self.limits.summary_tokens:
            raise ValueError('Summary/index exceeds its budget')
        return ids, spans

    def summary_request(self, question):
        return {'transcript_ids': list(self.target_ids),
                'entries': [{'id': f'M{i}', **asdict(e)} for i, e in enumerate(self.entries)],
                'summary_token_budget': self.limits.summary_tokens, 'question': question}

    def compact(self, question):
        with self.lock:
            try:
                return self._compact(question)
            except BaseException:
                if isinstance(self.archive, PagedArchive):
                    self.archive.db.rollback()
                    self.archive.cache.clear()
                self.cache_confirmed = False
                raise

    def _compact(self, question):
        """Select from the target transcript, summarize with provenance, then rebuild.

        Logical generation changes only after successful target prefill. Failed
        model calls can invalidate physical caches; the old prompt stays replayable.
        """
        with self.lock:
            self.cache_confirmed = False  # The summarizer may share the target engine.
            self._fits(self.target_ids + self._query(question), student=True)
            self._fits(self.target_ids + self._query(question), student=False)
            request = self.summary_request(question)
            entries = self._normalize_entries(self.summarize(request))
            summary_merges = 0
            while True:
                try:
                    seed, summary_spans = self._seed(entries)
                    break
                except ValueError as error:
                    if str(error) != 'Summary/index exceeds its budget' or len(entries) < 2:
                        raise
                    # External addresses have a real prompt cost. Coalesce adjacent
                    # items without dropping any generated text or provenance. Each
                    # round strictly reduces item count, so even an oversized single
                    # item fails closed rather than looping or truncating evidence.
                    entries = self._normalize_entries([
                        SummaryEntry('\n'.join(e.text for e in entries[i:i+2]),
                            tuple(dict.fromkeys(ref for e in entries[i:i+2] for ref in e.references)))
                        for i in range(0, len(entries), 2)])
                    summary_merges += 1
            # Select exact evidence from the WHOLE current target transcript.
            trace = self._trace(self.target_ids + self._query(question))
            handles = self._hits(self.target_spans, trace)
            handles = self._bounded(handles, prefix=seed, present=(), suffix=(),
                maximum=self.limits.compact_tokens,
                token_budget=self.limits.compact_tokens - len(seed), count=self.limits.max_candidates)
            evidence, placements = self._evidence(handles)
            rebuilt, exact = list(seed), {}
            self._extend(rebuilt, exact, evidence, placements)
            if len(rebuilt) > self.limits.compact_tokens:
                raise ValueError('Compacted seed exceeds its budget')
            self._fits(rebuilt + self._query(question), student=True)
            self._fits(rebuilt + self._query(question), student=False)
            try:
                # A successful decode forces native prefill; discard its probe token.
                if not list(self.target(rebuilt, 1)):
                    raise RuntimeError('Target did not confirm rebuild completion')
            except BaseException:
                self.cache_confirmed = False
                if hasattr(self.selector, 'reset'):
                    self.selector.reset()
                raise
            if hasattr(self.selector, 'reset'):
                self.selector.reset()
            self.entries = entries
            self.student_ids, self.target_ids = list(rebuilt), list(rebuilt)
            self.student_spans, self.target_spans = dict(exact), dict(exact)
            self.summary_spans = summary_spans
            self.generation += 1
            self.revision += 1
            self.cache_confirmed = True
            self.events.append({'kind': 'compact', 'generation': self.generation,
                                'exact': handles, 'seed_tokens': len(rebuilt),
                                'seed_hash': token_hash(rebuilt), 'summary_merge_rounds': summary_merges})
            self._save()
            return self.events[-1]

    def representation(self, handle):
        if handle not in self.archive.chunks:
            raise ValueError('Unknown archive chunk')
        if handle in self.target_spans:
            return 'exact'
        if any(handle in self.index.chunks(ref) for e in self.entries for ref in e.references):
            return 'summarized'
        return 'archive-only'

    def _snapshot(self):
        state = {'schema': 2 if isinstance(self.archive, PagedArchive) else 1,
                 'limits': asdict(self.limits), 'system': self.system,
                 'retrieval_mode': self.retrieval_mode, 'last_answer': self.last_answer,
                 'exact_lookup': self.exact_lookup_enabled,
                 'entries': [asdict(e) for e in self.entries], 'generation': self.generation,
                 'revision': self.revision, 'next_original': self.next_original, 'turn': self.turn,
                 'student_ids': self.student_ids, 'target_ids': self.target_ids,
                 'student_spans': self.student_spans, 'target_spans': self.target_spans,
                 'summary_spans': self.summary_spans}
        if isinstance(self.archive, PagedArchive):
            for name in ('student_ids','target_ids'):
                state[name + '_text_hash'] = hashlib.sha256(
                    b''.join(self.tok.token_bytes(t) for t in state[name])).hexdigest()
        else:
            state.update(manifests=self.index.nodes, source_cues=self.source_cues,
                         archive=self.archive.to_dict(), events=self.events)
        return state

    def _write_checkpoint(self, state):
        state = dict(state)
        state['state_hash'] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.checkpoint.with_suffix(self.checkpoint.suffix + '.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.checkpoint)
        directory = os.open(self.checkpoint.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _descriptor(self):
        return {'schema':2, 'storage':'sqlite', 'archive_id':self.archive.archive_id,
                'database':self.archive.path.name}

    def _save(self):
        if not self.checkpoint:
            return
        if isinstance(self.archive, PagedArchive):
            self.archive.save_session(self._snapshot())
            if not self.checkpoint.exists():
                self._write_checkpoint(self._descriptor())
        else:
            self._write_checkpoint(self._snapshot())

    @classmethod
    def _activate(cls, state, tokenizer, selector, target, summarize, *, path,
                  embedder, embedding_id, store=None):
        if state.get('schema') != (2 if store is not None else 1):
            raise ValueError('Unsupported session schema')
        obj = cls(tokenizer, selector, target, summarize, system=state['system'],
                  limits=Limits(**state['limits']), retrieval_mode=state['retrieval_mode'],
                  exact_lookup=state.get('exact_lookup', True),
                  embedder=embedder, embedding_id=embedding_id, _storage=store,
                  _search_path=Path(path).with_suffix('.search.sqlite3') if store is not None else None)
        obj.last_answer = state.get('last_answer')
        if store is None:
            obj.archive = OmissionArchive.from_dict(state['archive'], tokenizer)
            obj.search.close()
            obj.search = ArchiveSearch(obj.archive, path=Path(path).with_suffix('.search.sqlite3'),
                                      embedder=embedder, embedding_id=embedding_id)
            obj.source_cues = state.get('source_cues', {})
            obj.search.sync(obj.source_cues)
            obj.index = ArchiveManifests(obj.archive)
            obj.index.restore(state['manifests'])
            obj.events = state['events']
        obj.entries = [SummaryEntry(e['text'], tuple(e['references'])) for e in state['entries']]
        obj.entries = obj._normalize_entries(obj.entries, make_manifest=False)
        for name in ('generation', 'revision', 'next_original', 'turn',
                     'student_ids', 'target_ids', 'summary_spans'):
            setattr(obj, name, state[name])
        for name in ('student_spans', 'target_spans'):
            spans = state[name]
            ids = obj.student_ids if name == 'student_spans' else obj.target_ids
            if store is not None and hashlib.sha256(b''.join(tokenizer.token_bytes(t) for t in ids)).hexdigest() != state[name.replace('_spans','_ids') + '_text_hash']:
                raise ValueError('Archive tokenizer identity changed')
            for h, (a, b) in spans.items():
                if h not in obj.archive.chunks or not 0 <= a < b <= len(ids):
                    raise ValueError('Invalid generation span')
                raw = b''.join(tokenizer.token_bytes(t) for t in ids[a:b])
                if obj.archive.chunks[h].text.encode() not in raw:
                    raise ValueError('Generation span does not contain exact source')
            setattr(obj, name, {h: tuple(span) for h, span in spans.items()})
        obj._fits(obj.student_ids, student=True, reserve=0)
        obj._fits(obj.target_ids, student=False, reserve=0)
        if any(not 0 <= a < b <= len(obj.student_ids) for a, b in obj.summary_spans):
            raise ValueError('Invalid summary span')
        if len(obj.summary_spans) > len(obj.entries):
            raise ValueError('Invalid summary entries')
        obj.checkpoint = Path(path)
        obj.cache_confirmed = False
        if hasattr(selector, 'reset'):
            selector.reset()
        return obj

    @classmethod
    def restore(cls, path, tokenizer, selector, target, summarize, *, embedder=None, embedding_id=None,
                archive_cache_bytes=8 * 1024**2):
        path = Path(path)
        database = path.with_suffix('.archive.sqlite3')
        state, checksum = None, None
        if path.exists():
            state = json.loads(path.read_text())
            checksum = state.pop('state_hash', None)
            if checksum != hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest():
                raise ValueError('Session checkpoint checksum mismatch')
        if state is None or state.get('schema') == 2:
            if not database.exists():
                raise ValueError('Paged archive database is missing')
            store = PagedArchive(database, tokenizer, cache_bytes=archive_cache_bytes)
            try:
                if state is not None and (state.get('storage') != 'sqlite' or state.get('database') != database.name
                                          or state.get('archive_id') != store.archive_id):
                    raise ValueError('Paged archive identity mismatch')
                active = store.load_session()
                if active is None:
                    raise ValueError('Paged archive has no committed session')
                obj = cls._activate(active, tokenizer, selector, target, summarize,
                    path=path, embedder=embedder, embedding_id=embedding_id, store=store)
                if not path.exists():
                    obj._write_checkpoint(obj._descriptor())
                return obj
            except BaseException:
                store.close()
                raise
        if state.get('schema') != 1:
            raise ValueError('Unsupported session schema')
        # One-time legacy migration. Preserve the original JSON as a hard-linked
        # backup; publishing the tiny descriptor happens only after SQLite commit.
        store = PagedArchive(database, tokenizer, cache_bytes=archive_cache_bytes)
        prior = store.load_session()
        marker = store.db.execute("SELECT value FROM metadata WHERE key='legacy_state_hash'").fetchone()
        if prior is not None:
            if marker is None or marker[0] != checksum:
                store.close()
                raise ValueError('Existing paged archive does not match legacy checkpoint')
            obj = cls._activate(prior, tokenizer, selector, target, summarize, path=path,
                                embedder=embedder, embedding_id=embedding_id, store=store)
        else:
            obj = cls._activate(state, tokenizer, selector, target, summarize, path=path,
                                embedder=embedder, embedding_id=embedding_id)
            old = obj.archive
            try:
                for sid in old.sources:
                    source = old.sources[sid]
                    actual = store.add(source['path'], source['version'], source['text'], source['original_start'], source['turn'])
                    if actual != sid:
                        raise ValueError('Legacy archive source IDs are not contiguous')
                    for occurrence in source.get('occurrences', []):
                        store.db.execute('INSERT OR IGNORE INTO occurrences VALUES (?,?)',
                            (sid, json.dumps(occurrence, sort_keys=True, separators=(',', ':'))))
                store.db.execute("DELETE FROM events WHERE kind='archive'")
                for event in old.events:
                    store.events.append(event)
                for message in old.messages:
                    store.messages.append(message)
                store.delivered, store.placeholder_emitted = old.delivered, old.placeholder_emitted
                store.cache_epoch = old.cache_epoch
                for sid, cue in obj.source_cues.items():
                    store.cues[sid] = cue
                nodes, events = obj.index.nodes, obj.events
                obj.archive = store
                obj.index = ArchiveManifests(store)
                obj.index.restore(nodes)
                obj.source_cues = store.cues
                obj.events = store.session_events
                for event in events:
                    obj.events.append(event)
                obj.search.archive = store
                store.db.execute("INSERT OR REPLACE INTO metadata VALUES ('legacy_state_hash',?)", (checksum,))
                store.save_session(obj._snapshot())
            except BaseException:
                store.close()
                obj.search.close()
                raise
        backup = path.with_suffix('.legacy.json')
        if backup.exists():
            if backup.read_bytes() != path.read_bytes():
                obj.close()
                raise ValueError('Existing legacy backup does not match checkpoint')
        else:
            os.link(path, backup)
        obj._write_checkpoint(obj._descriptor())
        return obj
