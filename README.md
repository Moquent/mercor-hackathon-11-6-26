# DAGScope

**A causal reachability verifier for AI explanations of distributed-system incidents.**

An AI reads a log export and says "request B's warning caused the payment failure." It sounds right.
DAGScope runs a real asynchronous application, records every dependency explicitly, reconstructs the
execution DAG, and checks each claim deterministically. Every verdict comes with the graph path behind it.

It is not a logging dashboard, an LLM judge, or an anomaly detector. No model grades another model here.

> A graph path proves **recorded dependency reachability**, not counterfactual causation.
> Temporal proximity creates no edges. A missing edge in an incomplete trace proves nothing.

## Quick start

```bash
docker compose up --build          # add -d to detach; every service has a health check or startup ordering
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # or: make venv
make smoke                         # one real order through every service, then verified
make test                          # full test suite against the live stack
```

Open the dashboard at **http://localhost:8501**. Other ports: gateway 8000, orders 8001, inventory 8002, verifier 8003, Postgres 5433, Redis 6380.

### Running a model

```bash
cp .env.example .env               # set OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME
make evaluate                      # first attempts on all 12 tasks; one fresh conversation each
make evaluate-ablation             # optional: same tasks without the parents column
```

Works with any OpenAI-compatible endpoint. Stored attempts are never regenerated.

### Without Docker or credentials

```bash
make test-offline                  # runs against the captured real traces
make dashboard-local               # dashboard replays captured evidence
```

Without a model, the repo shows only `HANDCRAFTED_*` fixtures. They are labelled as not being model output on every screen and in every file, and no AI metric is ever computed from them.

## Architecture

```
client ─HTTP─▶ gateway ─HTTP─▶ orders ─XADD─▶ Redis Stream ─XREADGROUP/XAUTOCLAIM─▶ worker ─HTTP─▶ inventory
                  │              │                                                   │              │
                  └──────────────┴──────── events + event_dependencies ──────────────┴──────────────┘
                                                PostgreSQL (append-only)
                                                       │
                                     collector + verifier (NetworkX) ─▶ Streamlit dashboard
```

| Component | Path | Role |
|---|---|---|
| Gateway | `app/gateway` | Mints trace and request ids and the root event; propagates parent ids |
| Orders | `app/orders` | Order transaction; publishes logical messages |
| Inventory | `app/inventory` | Row-locked reservations; a failure records the committed write it read |
| Worker | `app/workers` | Delivery attempts, idempotency ledger, retries, simulated payment gateway |
| Instrumentation | `app/instrumentation` | Event and edge recording; labelled fault plans |
| Verifier | `app/verifier` | DAG construction, integrity validation, claim verification |
| Scenarios | `app/scenarios` | Scenario runner, independent ground truth, the 12 task specs |
| Evaluation | `app/evaluation` | Prompting, first-attempt protocol, scoring, report |
| Dashboard | `app/dashboard` | Overview, DAG, AI vs truth, runner, results |

### Why the evidence can be trusted

- **Commit events are atomic with commits.** A `committed` event is inserted in the same PostgreSQL transaction as the business write, so it exists only if the write committed. Rollbacks get their own `rolled_back` event, in a separate transaction.
- **Transaction ids are real.** `database_transaction_id` is `pg_current_xact_id()`, and captures record Postgres's own `pg_xact_status` for each one.
- **Observability tables are append-only**, enforced by a database trigger.
- **Edges come only from explicit instrumentation**: HTTP headers, stream fields, or a row's `last_commit_event_id`. No code derives one from timestamps, and a test shows the graph is unchanged when timestamps are shuffled.
- **Logical messages and deliveries are separate.** One `message_id` can have many `delivery_attempt_id`s and at most one committed effect, guaranteed by a primary key on the idempotency ledger.

## Verdicts

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | The recorded evidence establishes the claim. A path is returned. |
| `CONTRADICTED` | The claim conflicts with evidence sufficiently complete to decide it. |
| `NOT_OBSERVED` | No path is visible, but coverage is incomplete, so nothing is concluded. |
| `INVALID_CLAIM` | Malformed, unavailable ids, or unevaluable (such as `caused` or `root_cause`). |

Completeness is explicit and earned. Unknown coverage is treated as open-world, and integrity failures (dangling references, cycles, duplicate ids) revoke closed-world status even if the metadata claims it.

Relationships: `reachable`, `not_reachable`, `insufficient_evidence`, `direct_dependency`, `shared_ancestor`, `no_shared_ancestor`, `produced_commit`, `not_produced_commit`.

`produced_commit` exists because reachability cannot answer "which attempt committed?": a failed attempt really does reach the eventual commit, through the retry edge. Attribution also requires a committed target, a matching delivery attempt, and a path that doesn't cross a retry edge.

```bash
curl -s localhost:8003/verify -H 'content-type: application/json' -d '{
  "run_id": "<run>",
  "coverage": {"closed_world": true, "level": "complete"},
  "claims": [{"source_event_id": "<id>", "target_event_id": "<id>", "relationship": "reachable"}]
}'
```

## Scenarios and benchmark

| # | Trap | What actually runs |
|---|---|---|
| 1 | Temporal correlation | Overlapping requests; B warns about the payment gateway just before A's payment fails, with no edge between them |
| 2 | Retry and duplicates | Attempt 1 writes and rolls back; attempt 2 commits but isn't ACKed; attempt 3 is a duplicate that gets skipped |
| 3 | Partial observability | A clean execution exported with an event or link removed; the complete graph stays private |
| 4 | Shared ancestor | One order fans out to reserve and charge; both fail close together, independently |

`benchmark/` contains: `runs/` (captured evidence), `tasks/` (AI-visible), `ground_truth/` (private), `model_attempts/`, and `results/` (`REPORT.md`, metrics, test summaries).

Regenerate with `make benchmark` (refused once real attempts exist, since they quote event ids from those runs). `make live` runs scenarios without touching the canonical set.

## External track: RCAEval

Real traces from [RCAEval](https://github.com/phamquiluan/RCAEval) (Pham et al., WWW 2025; MIT licence), reported separately from the primary benchmark. Span `parentSpanID` values become dependency edges, and coverage is always open-world. `load_dataset("phamquiluan/RCAEval")` only returns the case index, so the fetch script downloads telemetry for the fixed 16-case sample at a pinned revision.

```bash
make rcaeval-fetch       # ~200 MB into ~/.cache/dagscope/rcaeval
make rcaeval-validate    # integrity, independent oracle and masking checks on 7.76M spans
make rcaeval-evaluate    # first attempts on the 23 external tasks
```

The raw telemetry isn't in this repo. Tasks, extracts, attempts and results are in `benchmark/rcaeval/`, and the offline tests run from those.

## Demo

See [DEMO.md](DEMO.md). The write-up is [SUBMISSION.md](SUBMISSION.md).
