"""Compatibility imports for the packaged SpecPrefill runtime."""
from memory_gateway.specprefill.compaction_adapters import (
    AdaptiveSelector,
    NativeSummarizer,
    NativeTarget,
)

__all__ = ['AdaptiveSelector', 'NativeTarget', 'NativeSummarizer']
