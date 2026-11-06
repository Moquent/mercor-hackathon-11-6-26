# realsystem: DAGScope's instrumented order system

This is the real distributed system from **DAGScope** by Sarvesh (branch `origin/sarvesh`, commit `5310275`,
"DAGScope: causal reachability verifier"). It was copied unchanged with
`git archive origin/sarvesh app requirements.txt Dockerfile .env.example`.

What it is:

- **gateway** (FastAPI, :8000) takes `POST /orders` and forwards them to orders.
- **orders** (:8001) writes the order to Postgres and publishes a message to a Redis stream.
- **inventory** (:8002) reserves stock (`PUT/GET /inventory/{sku}` to seed or read it).
- **worker** consumes the stream with a consumer group. It handles retries through XAUTOCLAIM redelivery and the commits that go with them.
- **verifier** (:8003) reads the recorded events back out of Postgres. It serves `/runs`, `/runs/{run_id}/graph` and `/verify`.
- **Postgres** (host :5433) and **Redis** (host :6380).

Every service records its own events and their parent -> child dependencies through `app/instrumentation/`
(request propagation, message delivery, retry, and the transaction that committed). Nothing outside the services writes
events. Each order can carry a **fault plan** (`faults`), such as a payment failure, a payment latency, a warning,
a rendezvous barrier, rolled-back attempts, a skipped ACK after commit, or a split fulfilment. This is how the four
scenarios produce temporal-correlation, retry, partial-observability and shared-ancestor traps from real execution.

Run it with:

    docker compose --profile realsystem up -d
    python -m rootproof.dagscope.runner scenario_1   # saves cases/dagscope/runs/live_<run_id>.json

The compose services are prefixed `rs-` and their host names are rewired to match (for example `rs-postgres`, `rs-redis`).
DAGScope's Streamlit dashboard is not included.
