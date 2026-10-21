"""Content-addressed provenance manifests, stored outside model prompts.

Manifest edges point to immutable source ranges or earlier manifests, never to
summary text or model cache positions. Every traversal has an explicit visited
set. This preserves addressability, not semantic retrieval guarantees.
"""
from __future__ import annotations

import hashlib
import json


class ArchiveManifests:
    def __init__(self, archive):
        self.archive = archive
        self.nodes = archive.manifests if hasattr(archive, 'manifests') else {}

    @staticmethod
    def key(references):
        return 'A' + hashlib.sha256(json.dumps(list(references), separators=(',', ':')).encode()).hexdigest()

    def intern(self, references):
        refs = tuple(dict.fromkeys(references))
        if not refs:
            raise ValueError('Empty archive manifest')
        for ref in refs:
            if ref.startswith('A'):
                if ref not in self.nodes:
                    raise ValueError('Unknown archive manifest')
            else:
                self.archive.resolve(ref)
        handle = self.key(refs)
        self.nodes[handle] = refs
        return handle

    def chunks(self, reference):
        if hasattr(self.archive, 'iter_references'):
            yield from self.archive.iter_references((reference,))
            return
        stack = [reference]
        visited, emitted = set(), set()
        while stack:
            ref = stack.pop()
            if ref in visited:
                continue
            visited.add(ref)
            if ref.startswith('A'):
                if ref not in self.nodes:
                    raise ValueError('Unknown archive manifest')
                stack.extend(reversed(self.nodes[ref]))
            else:
                for h in self.archive.resolve(ref):
                    if h not in emitted:
                        emitted.add(h)
                        yield h

    def restore(self, nodes):
        # Insertion order is topological: new manifests only reference existing IDs.
        self.nodes = self.archive.manifests if hasattr(self.archive, 'manifests') else {}
        for key, refs in nodes.items():
            if key != self.key(refs) or self.intern(refs) != key:
                raise ValueError('Archive manifest checksum mismatch')
