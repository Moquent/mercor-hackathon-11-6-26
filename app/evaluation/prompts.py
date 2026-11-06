"""Prompt construction. Takes only the public task: the private ground truth is not an argument."""
from __future__ import annotations

from typing import Any

CONDITIONS = {
    "explicit_parents": "Each log line lists parents=<event ids>: those are the recorded dependency edges (parent -> this event).",
    "chronological_only": "This export omits the dependency-edge column. Dependency edges were recorded, but you must judge them from the remaining log fields.",
}
COVERAGE_TEXT = {
    "complete": "COMPLETE. This is a closed-world export: every instrumented event and every recorded dependency of this execution is included.",
    "partial": "PARTIAL. The export came from a sampling collector: some events or dependency links of this execution may be missing from it.",
}
DEFINITIONS = """Relationship definitions (the source and target are event ids; edges point parent -> dependent child):
- reachable: this export establishes a directed path of recorded dependencies from source to target.
- direct_dependency: this export establishes a single recorded edge from source to target.
- not_reachable: this export establishes that no such path exists in the execution.
- insufficient_evidence: this export establishes neither reachable nor not_reachable.
- produced_commit: the source event belongs to the delivery attempt that itself performed the committed database transaction recorded by the target event.
- not_produced_commit: the source event's delivery attempt did not perform that committed transaction.
- shared_ancestor: a third, earlier event is recorded upstream of both source and target; give its id in ancestor_event_id.
- no_shared_ancestor: no event is recorded upstream of both source and target."""


def build_prompt(task: dict[str, Any], condition: str = "explicit_parents") -> str:
    logs = task["ai_visible_logs"] if condition == "explicit_parents" else task["ai_visible_logs_no_parents"]
    pairs = []
    for i, pair in enumerate(task["pairs"], 1):
        line = (f"{i}. source_event_id={pair['source_event_id']} target_event_id={pair['target_event_id']} "
                f"relationship: one of {', '.join(pair['allowed_relationships'])}")
        if pair["ancestor_required"]:
            line += " (include ancestor_event_id if you choose shared_ancestor)"
        pairs.append(line)
    return f"""You are helping investigate an incident in an asynchronous distributed order-processing system (API gateway, order service, inventory service, event worker; PostgreSQL and Redis Streams).

Question: {task['question']}

Coverage of this log export: {COVERAGE_TEXT[task['coverage']]}
{CONDITIONS[condition]}

{DEFINITIONS}

Log export:
```
{logs}
```

Report exactly one claim per listed item:
{chr(10).join(pairs)}

Respond with only a JSON object of this form:
{{"claims": [{{"source_event_id": "...", "target_event_id": "...", "relationship": "...", "ancestor_event_id": null}}], "explanation": "...", "confidence": 0.0}}
confidence is your overall confidence between 0 and 1."""
