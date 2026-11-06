"""Ground truth computation. Deliberately imports nothing from app.verifier and not NetworkX:
it uses a hand-written BFS over raw edge rows and the persisted database state. The test suite then
requires this oracle, the SQL recursive-CTE closure, and the NetworkX verifier to agree."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def adjacency(deps: list[dict[str, Any]]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for d in deps:
        adj[d["parent_event_id"]].append(d["child_event_id"])
    return adj


def bfs_path(adj: dict[str, list[str]], source: str, target: str) -> list[str] | None:
    if source == target:
        return None
    prev: dict[str, str] = {}
    queue, seen = deque([source]), {source}
    while queue:
        node = queue.popleft()
        for nxt in adj.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = node
            if nxt == target:
                path = [target]
                while path[-1] != source:
                    path.append(prev[path[-1]])
                return path[::-1]
            queue.append(nxt)
    return None


def closure(deps: list[dict[str, Any]]) -> set[tuple[str, str]]:
    adj = adjacency(deps)
    pairs: set[tuple[str, str]] = set()
    for start in {d["parent_event_id"] for d in deps}:
        stack, seen = list(adj.get(start, [])), set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            pairs.add((start, node))
            stack.extend(adj.get(node, []))
    return pairs


def ancestors_of(deps: list[dict[str, Any]], node: str) -> set[str]:
    rev: dict[str, list[str]] = defaultdict(list)
    for d in deps:
        rev[d["child_event_id"]].append(d["parent_event_id"])
    stack, seen = list(rev.get(node, [])), set()
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(rev.get(n, []))
    return seen


def apply_mask(artifact: dict[str, Any], mask: dict[str, Any] | None) -> tuple[list[dict], list[dict], list[str]]:
    """The AI-visible subset of a run. Returns (events, dependencies, hidden_event_ids)."""
    if not mask:
        return artifact["events"], artifact["dependencies"], []
    names = artifact["manifest"]
    hidden = {names[n] for n in mask.get("hide_events", [])}
    hidden_edges = {(names[a], names[b]) for a, b in mask.get("hide_edges", [])}
    events, deps = [], []
    for e in artifact["events"]:
        if e["event_id"] in hidden:
            continue
        parents = [p for p in e["parent_event_ids"] if (p, e["event_id"]) not in hidden_edges]
        if mask.get("scrub_dangling_refs", True):
            parents = [p for p in parents if p not in hidden]
        events.append({**e, "parent_event_ids": parents})
    for d in artifact["dependencies"]:
        pair = (d["parent_event_id"], d["child_event_id"])
        if pair in hidden_edges or pair[0] in hidden or pair[1] in hidden:
            continue
        deps.append(d)
    return events, deps, sorted(hidden)


def committing_attempt(artifact: dict[str, Any], commit_event_id: str) -> str | None:
    """Which attempt committed this write, from the idempotency ledger -- only if Postgres says the xid committed."""
    for row in artifact["state"]["processed_messages"]:
        if row["commit_event_id"] == commit_event_id and artifact["state"]["xact_status"].get(str(row["xid"])) == "committed":
            return row["delivery_attempt_id"]
    return None
