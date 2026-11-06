# DAGScope: three-minute demo

**Before you start:** `docker compose up --build -d`, then open http://localhost:8501. Have the **Execution DAG** and **Real Traces (RCAEval)** tabs ready. Every number below is from `benchmark/results/REPORT.md`.

## 0:00–0:30 · A realistic incident

*Show: **Execution DAG**, Scenario 1. The warning → payment failure claim is pre-selected.*

> "Two orders overlap and one payment fails with a gateway timeout. About 60 milliseconds earlier, the other request logs a connection-pool warning on the same gateway. Any engineer would connect those. These are real events from FastAPI services, Postgres and Redis Streams, with labelled injected faults."

## 0:30–1:15 · The AI's first explanation

*Show: **Real Traces (RCAEval)**. `rcaeval_attr_re3ob_currencyservice_f1_1` opens by default. Read the explanation aloud, then point at the verdict and the table.*

> "Now real data: a third-party benchmark of 7.7 million spans, where the fault was deliberately injected, so the label is an experiment and not an opinion. We asked GPT-5 mini which service failed. It's a first answer in a fresh conversation, stored verbatim. It says checkout, at 95% confidence, and the reasoning is really convincing. It's wrong: the fault was in currency service. Those errors, zero to 2,918, were in the table it read."

## 1:15–2:00 · Reveal the DAG and verify

*Show: **Execution DAG**, Scenario 1. Point at the CONTRADICTED verdict. Change the source to `A.request_received` to show the blue path, then set the relationship to `caused`.*

> "DAGScope doesn't ask another AI whether that sounded right. It rebuilds the dependency graph and returns a proof. Warning to failure: contradicted, because the trace is complete and has no path. Request root to failure: supported, with every hop. And if I claim `caused`, it refuses, because a path is reachability, not causation."

*Scroll to Intervention runs.*

> "Causal evidence comes from experiments. Remove request B and the failure persists. Remove the fault and it disappears, with the warning still there."

## 2:00–2:40 · Retries and missing evidence

*Show: Scenario 2, `A.created.a1.message_delivered` → commit. Toggle `reachable`, then `produced_commit`.*

> "Attempt one wrote and rolled back, and Postgres confirms that transaction aborted. It still reaches the commit through the retry edge, but it didn't produce it. Plain graph search can't tell those apart."

*Show: Scenario 3, evidence view `scenario_3_task_1`, relationship `reachable`.*

> "Here one event is hidden from the AI, shown dashed. The verdict is NOT_OBSERVED: a missing edge doesn't prove disconnection, and a plausible path isn't proof. This matters in practice: 10 of our 16 real cases had dangling parent links, over 126,000 in total."

## 2:40–3:00 · Results and why it matters

*Show: **Experimental Results**, then back to **Real Traces (RCAEval)**.*

> "With dependency links written out, the model got 12 of 12. Remove them and it starts overclaiming: 10 of 12. On real traces it found the injected service 10 times out of 16, and its confidence was about the same whether it was right or wrong: 0.87 versus 0.84. That's a small sample, so these are failure modes we observed, not a verdict on the model. The checker itself had no false verdicts across 16,000 masked claims. You can't tell from the confidence. You can check the proof."
