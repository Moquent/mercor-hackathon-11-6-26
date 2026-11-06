"""HTTP face of the collector + verifier."""
from __future__ import annotations

import networkx as nx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.database.session import engine, init_db
from app.verifier.claims import verify_claims
from app.verifier.collector import graph_for_run
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import validate_graph_integrity

app = FastAPI(title="DAGScope verifier")


class VerifyRequest(BaseModel):
    run_id: str
    claims: list[dict]
    # Default open-world: the caller must assert completeness explicitly.
    coverage: CoverageMetadata | None = None


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/runs")
def runs() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT metadata->>'run_id' AS run_id, count(*) AS events, min(timestamp) AS started FROM events "
            "GROUP BY 1 ORDER BY started DESC LIMIT 50"))
        return [{"run_id": r.run_id, "events": r.events, "started": r.started.isoformat()} for r in rows]


@app.get("/runs/{run_id}/graph")
def run_graph(run_id: str) -> dict:
    graph, events, deps = graph_for_run(run_id)
    if not events:
        raise HTTPException(status_code=404, detail="unknown run")
    return {"integrity": validate_graph_integrity(graph, events), "events": events, "dependencies": deps,
            "is_dag": nx.is_directed_acyclic_graph(graph)}


@app.post("/verify")
def verify(req: VerifyRequest) -> dict:
    graph, events, _ = graph_for_run(req.run_id)
    if not events:
        raise HTTPException(status_code=404, detail="unknown run")
    return {"results": verify_claims(req.claims, graph, req.coverage)}
