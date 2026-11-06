"""Verifies a fault-origin claim against an intervention label.

This is deliberately separate from graph reachability. A dependency path can't say where a fault
originated, but a controlled experiment that injected the fault can. The verdict therefore carries
a different evidence class, and it describes that experiment only.
"""
from __future__ import annotations

from typing import Any

from app.verifier.evidence import Verdict


def verify_fault_origin(claimed_service: Any, injected_service: str, candidates: list[str]) -> dict[str, Any]:
    claim = {"relationship": "fault_injected_in", "service": claimed_service}
    base = {"claim": claim, "evidence_class": "intervention_label", "injected_service": injected_service}
    if not isinstance(claimed_service, str) or claimed_service not in candidates:
        return {**base, "verdict": Verdict.INVALID_CLAIM.value, "explanation": f"{claimed_service!r} is not one of the listed services."}
    if claimed_service == injected_service:
        return {**base, "verdict": Verdict.SUPPORTED.value, "explanation": f"The experiment injected the fault into {injected_service}."}
    return {**base, "verdict": Verdict.CONTRADICTED.value,
            "explanation": f"The experiment injected the fault into {injected_service}, not {claimed_service}."}
