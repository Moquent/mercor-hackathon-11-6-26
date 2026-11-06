# DAGScope: Mercor Hack Day submission

**Georgia Tech · November 6, 2026**

## The professional verification problem

During an incident, engineers increasingly ask AI to read logs and explain what happened. In asynchronous systems the wrong answer is unusually convincing, and acting on it means rolling back the wrong service or closing the incident on the wrong cause.

**Research question:** Can AI models correctly identify event dependencies in asynchronous distributed systems, or do they mistake temporal proximity, retries, and incomplete observations for causal evidence?

## Why incorrect answers are plausible

| Trap | The plausible wrong answer | Why it's wrong |
|---|---|---|
| Temporal correlation | "The pool warning 60 ms earlier caused the gateway timeout." | The two requests share no recorded dependency, and the failure persists when the other request is removed. |
| Retry attribution | "Attempt 1 wrote the row, so it produced the change." | That transaction aborted. The commit belongs to attempt 2. |
| Partial observability | "No visible edge, so they're disconnected" or "same trace, so obviously connected." | The export is incomplete, so neither follows. |
| Shared ancestor | "The stock failure caused the rejected payment." | Both descend from the same order. Neither reaches the other. |

## How ground truth is established

Each scenario runs through real services (FastAPI, PostgreSQL, Redis Streams). Faults are injected under labelled plans, and traces are never fabricated. Ground truth is computed, never taken from an LLM:

- **Reachability:** a hand-written BFS (`app/scenarios/ground_truth.py`) that imports neither NetworkX nor the verifier, cross-checked at generation time against a PostgreSQL recursive CTE and NetworkX. All three must agree on every reachable pair, or generation refuses to run.
- **Commit attribution:** the idempotency ledger plus PostgreSQL's own `pg_xact_status`, independent of any event's self-reported status.
- **Partial observability:** the AI sees a masked export while the complete graph stays private. The correct answer depends on what the *visible* evidence establishes.

Tasks and private ground truth are in separate files, and a test confirms that no prompt contains an answer, a logical event name, a fault label, or a hidden event id.

## How the checker verifies claims

The AI must answer in structured claims, each evaluated independently into one of four verdicts: SUPPORTED, CONTRADICTED, NOT_OBSERVED or INVALID_CLAIM, returned with the path and per-hop evidence.

- **Negative conclusions require closed-world coverage.** Unknown coverage is open-world, and an integrity failure revokes closed-world status.
- **Reachability is not attribution.** `produced_commit` requires a committed target, the same delivery attempt, and a path that doesn't cross a retry edge.
- **Reachability is not causation.** `caused` and `root_cause` return INVALID_CLAIM. The only causal evidence is from separate intervention runs.

### Correctness and fairness of the checker

- **Test suite:** 278 passed, 0 failed, 0 skipped against the live stack (`benchmark/results/verifier_tests.json`).
- **Accepts correct answers:** the computed answer key is SUPPORTED on all 12 tasks (32/32 claims).
- **Rejects incorrect answers:** a hand-written plausible wrong answer fails on all 12 tasks. Separately, every other allowed relationship for every pair is tested and none is accepted.
- **Mutation check:** 16 of 16 planted verifier bugs were caught by the offline tests, after an unmutated control run passed (`benchmark/results/mutation_check.json`).

Testing also found three real application bugs, all fixed before the benchmark run: a failed order step overwritten by a later success, an unhandled race on concurrent charges, and an idempotency-race loser committing a second commit event.

Fairness: the prompt defines every relationship, states the export's coverage, and lists exactly which pairs to evaluate. Scenario 3 includes pairs that *are* provable, so always answering "insufficient" fails.

## Experiments

All numbers come from `benchmark/results/REPORT.md`. Model: `openai/gpt-5-mini` via OpenRouter, evaluated 2026-11-06 UTC. Total API cost across all runs: about $0.21.

### Primary benchmark: 12 tasks on our own instrumented system

| Log format | Tasks correct | Claims accepted | False-positive reachability | False-negative reachability | Incorrect certainty | Format failures |
|---|---|---|---|---|---|---|
| With `parents` column | 12/12 | 32/32 | 0/10 | 0/10 | 0/2 | 0/12 |
| Without `parents` column | 10/12 | 30/32 | 0/12 | 0/10 | 2/4 | 0/12 |

With dependency links written out, the model made no errors on any trap. With them removed, it treated shared correlation ids as proof: on `scenario_3_task_2` it said a same-`message_id` sequence "establishes" a path at 0.95 confidence, on a partial export where that link was missing. Without the column, `insufficient_evidence` is arguably the right reading more often, so this condition is best read as a stress test.

### External track: real traces from RCAEval

[RCAEval](https://github.com/phamquiluan/RCAEval) (Pham et al., WWW 2025) is a third-party benchmark of microservice failures with injected faults. We used a sample of 16 cases, selected by a fixed rule before any model ran (one per root-cause service, repetition 1; 7.76 million spans, pinned revision). The labels come from the experiment that injected each fault, which is intervention evidence that doesn't depend on our verifier or instrumentation.

| Measure | Result |
|---|---|
| Injected service identified, overall | 10/16 |
| Baseline with no model: blame the service with the most new errors | 5/16 |
| Model, on cases where that baseline is wrong | 6/11 |
| Model, when the injected service reports no spans | 2/2 |
| Reachability and real-gap tasks fully correct | 2/7 |
| Incorrect certainty on partial traces | 10/17 claims |
| Mean stated confidence: right vs. wrong attributions | 0.87 vs. 0.84 |

Observed failure modes in these cases:

- **Caller blame stated with high confidence.** On `re3ob_currencyservice_f1_1` the model named `checkoutservice` at 0.95 confidence, although `CurrencyService/Convert` errors rising from 0 to 2,918 were in the table it was given.
- **Confidence didn't separate right from wrong.** Six wrong attributions averaged 0.84 confidence, so stated confidence would not have told an on-call engineer which ones to doubt.
- **Absence treated as disproof.** All five reachability misses share one pattern: "different trace, so no path" and "siblings, so no path" on an export declared partial. The model did handle both tasks with a visible orphaned span correctly (2/2). These 10 claims are unproven rather than shown false, and a practising engineer might make the same call about siblings. We score them against the stated definitions, and report them as NOT_OBSERVED, not CONTRADICTED.

### The verifier on real traces

No model and no human labels are involved in these checks (`benchmark/rcaeval/results/verifier_on_real_traces.json`).

- **Scale:** 7,763,649 spans and 7,579,851 edges across 16 cases, with 0 cycles.
- **Real gaps, no masking by us:** 126,236 dangling parent links, in 10 of the 16 cases, which is direct support for refusing to assume traces are complete.
- **Independent oracle:** 0 disagreements between NetworkX and a separate search, over 32,000 exact descendant sets.
- **Masking soundness:** with 10% of spans hidden, over 16,000 claim pairs: 0 false SUPPORTED, 0 wrongly CONTRADICTED, 0 negative claims accepted. 194 pairs with a true path correctly became NOT_OBSERVED.
- **False completeness:** asserting closed-world on all 10 gapped cases was revoked every time.


Protocol: the same model on every task; a fresh conversation with a single user message; the first response stored verbatim with the exact prompt, model name, UTC date and token usage. Only documented transport failures are retried, and a stored attempt is never regenerated. Output that can't be parsed counts as a separate format failure. Artifacts are in `benchmark/model_attempts/` and `benchmark/results/REPORT.md`.

## Limitations

- **Small benchmark:** 12 primary tasks from one captured run per scenario, plus 23 external tasks from 16 cases and a single model. Results describe failure modes observed in these cases, not a model's general causal reasoning. No confidence intervals are claimed.
- **External attribution uses traces only.** RCAEval also offers metrics and logs; the model saw a trace summary and one example trace, and some faults (such as disk or memory stress) may leave weak signals there, so 10/16 isn't a statement about what's achievable with full telemetry.
- **External reachability is strict by definition.** There's no private complete graph for third-party traces, so those tasks test what the export establishes, not what actually happened.
- **Controlled application:** faults are injected and the payment gateway is simulated. Interventions show what this system does, not a production root cause.
- **"Complete" means complete relative to the instrumentation.** A dependency the services never recorded can't be seen. Closed-world is a statement about the trace, not about reality.
- **Shared evidence base:** the oracles are independent implementations but read the same recorded edges, so an instrumentation mistake could affect them all. Commit attribution has an independent check through Postgres. Integrity validation and the end-to-end tests reduce this risk but don't eliminate it.
- **Timing control:** scenario 1 uses a bounded harness barrier so the two requests overlap reliably. It sits outside the application's dependency model and affects when events happen, not whether they do. The intervention run removing request B omits it.
- **The misleading text is deliberate:** the warning and hold-rejected messages are injected to tempt a wrong reading, which is the point of the benchmark, but they aren't real production logs.
