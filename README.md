# RootProof: verify an AI's incident diagnosis by replaying the outage with its fix applied

AI on-call agents write confident root-cause analyses. Reading one can't tell you whether it is right. RootProof checks an AI's root-cause
analysis three ways:

1. **Replay (simulated incidents).** It replays a seeded incident with the AI's proposed fix applied. The fix counts only if users stop
   seeing errors.
2. **Causal-chain check.** It checks the propagation chain the AI claims ("X caused Y") against the true propagation graph.
3. **Real telemetry (RCAEval).** It checks the AI's claims against real fault-injection data from the RCAEval benchmark. Recorded data
   can't be replayed, so these checks are reference-free, and they're scored against the benchmark's labels.

Every rejection comes with a certificate: replay numbers, a timing contradiction, a retry-amplification ratio, an impossible edge, or a
metric signature that contradicts the claimed fault.

## Run it (Python ≥ 3.10, standard library only)

```bash
python3 rootproof/evaluate.py      # re-verifies every saved AI answer and rebuilds results/ + ground_truth.md (~40 s)
open results/report.html           # the interactive results site (works offline)
```

No API keys are needed to re-verify, because Jev's responses are cached in `results/jev_cache.json`. To redo everything live, add
`OPENAI=...` and `TYPESAFE=...` to `.env` and run `python3 rootproof/run_ai.py 3` and `python3 rootproof/rcaeval_run.py`.
Rebuilding the RCAEval packets from raw data needs pandas/pyarrow: download the `re2ob_*_{1,2}` cases from
`huggingface.co/datasets/phamquiluan/RCAEval` into `data/rcaeval/`, then run `python rootproof/rcaeval_prepare.py data/rcaeval`.

## Results (subject AI: gpt-6-luna; extraction: TypeSafe Jev)

**Simulated incidents** (15 tasks × 3 fresh runs, fictional "Larkwave" streaming backend)
- First attempts: **12/15 accepted**. Caught: 02, 13 and 15. Across all runs: 34/45 accepted.
- Verifier self-test: **30/30**. Every reference answer was accepted and every trap answer was caught.
- Jev extraction audit: **313/315** decisions match the hand labels.
- Causal chains: the AI's claimed edges have **97% precision but only 65% recall**. It describes the visible failure path and leaves out
  the retry and fallback **load edges**, which are the feedback loops.

**Real telemetry: RCAEval RE2, Online Boutique** (30 held-out cases, plus 30 dev cases used to design the rules)

| Metric | Result |
|---|---|
| AI names the right service | 100% (z-score baseline: 73% at top-1, 100% at top-3) |
| AI names the right service **and** fault type | 70%. It labels memory, socket and loss faults as "delay". |
| Verifier agrees with the label | **87%** |
| Flags that are real errors | 86% (6 of 7) |
| Wrong answers caught (recall) | 67% (6 of 9) |
| Answers the verifier passed that are correct | 87%, against a 70% base rate |

**Findings**
1. **The model fixes the trigger and misses the feedback loop.** When a config change is visibly the trigger, it reverts the change and stops.
   Layered retries are already driving about 8x load, so in replay errors stay at 100%. Tasks 02 and 13 failed in every run.
2. **Right fix, wrong story.** In 15 (a loud retry storm hiding a quiet credential fault), the actions work but the named root cause is
   contradicted: users saw errors 30 s before the blamed service degraded. Grading only the outcome would mark this answer correct.
3. **On real data, the fault type is the weak spot, not localization.** The checks catch most of these mislabels without seeing the label.

## What's in the box

| Path | What it is |
|---|---|
| `rootproof/sim.py` | Seeded fluid-queue simulator: retries, timeouts, fallback chains, rate limiters, non-retryable errors, fault injection, counterfactual replay |
| `rootproof/incidents.py` | 15 incidents (6 traps, 2 controls, 5 hypothesis tests, 2 multi-fault) and the 21-action catalog |
| `rootproof/render.py`, `run_ai.py` | On-call packet renderer and subject-AI harness (one fresh request per task, unedited answers) |
| `rootproof/verify.py` | Jev extraction → replay (decides) → claim checks + certificates (explain) |
| `rootproof/chain.py` | Claimed vs. true causal-propagation graph: edge precision and recall |
| `rootproof/rcaeval_*.py` | RCAEval track: prepare packets, run the AI, reference-free claim checks, scoring vs. labels and a z-score baseline |
| `rootproof/selftest.py` | Reference and trap answers per task, plus hand labels for the extraction audit |
| `rootproof/evaluate.py`, `site_build.py` | One command for every result, plus the interactive report |
| `tasks/` | Exact prompts: `tasks/NN-*/prompt.txt` (simulated) and `tasks/rcaeval/<case>/prompt.txt` (real) |
| `attempts/` | Unedited AI answers with model, timestamp and prompt hash (`NN.md` is the official first attempt) |
| `ground_truth.md` | Right answer for every simulated task, why it is right, and the computed minimal fixes |
| `results/` | `report.html` (site), `summary.json` (every verdict and metric), `jev_cache.json` |

## Honest limitations
- The simulated systems are a fluid model, not a real cluster, and all names are fictional. Real data can't be replayed, so the RCAEval
  track checks claims, not fixes.
- One subject model. Tasks 12–15 were written after the first results, to test the hypotheses. The report includes both the confirmed and
  the refuted cases (09 and 10 passed).
- The RCAEval signature rules were designed on the dev split (instance 2). Two later fixes for flat or absent metric series apply to both splits.
- Actions outside the 21-action catalog are ignored. That can under-credit an unusual but valid fix; the extraction audit is how we check this.
