"""Provider transport, structured clients, and foreground telemetry."""

from .foreground import append_record, finalize_record, substitution_record, telemetry_path
from .openai import ModelProtocolError, ModelTransportError
from .prompt_cache import (
    PromptCacheReport,
    block_content_hash,
    build_prompt_cache_report,
    merge_provider_cache_usage,
)
from .structured import OpenAICompatStructuredClient

__all__ = [
    "ModelProtocolError",
    "ModelTransportError",
    "OpenAICompatStructuredClient",
    "PromptCacheReport",
    "append_record",
    "block_content_hash",
    "build_prompt_cache_report",
    "finalize_record",
    "merge_provider_cache_usage",
    "substitution_record",
    "telemetry_path",
]
