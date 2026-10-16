from __future__ import annotations

from memory_gateway.admission import (
    CAPSULE_SCHEMA_V2,
    CapsuleEnvelopeV2,
    admit_capsule_v2,
    run_selector_chunk_gate,
)
from memory_gateway.assembly import ContextSnapshot, SubstitutionReport
from memory_gateway.storage import SQLiteStore
from memory_gateway.transport import (
    build_prompt_cache_report,
    finalize_record,
    substitution_record,
)


def test_namespace_packages_reexport_public_api() -> None:
    assert CAPSULE_SCHEMA_V2 == "orchid.capsule.v2"
    assert callable(admit_capsule_v2)
    assert callable(run_selector_chunk_gate)
    assert callable(build_prompt_cache_report)
    assert callable(substitution_record)
    assert callable(finalize_record)
    assert ContextSnapshot is not None
    assert SubstitutionReport is not None
    assert SQLiteStore is not None
    assert CapsuleEnvelopeV2 is not None
