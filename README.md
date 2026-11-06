# RootProof

An AI on-call agent writes a confident root-cause analysis. RootProof checks it without trusting the prose.

1. **Replay.** It replays a seeded incident with the AI's proposed fix applied. The fix counts only if users stop seeing errors, and the result is re-checked under five simulator seeds.
2. **Causal chain.** It compares the propagation chain the AI claims with the true one from the simulation.
3. **Real telemetry.** On RCAEval (real fault injections on Online Boutique) it checks the AI's claims against the metrics and the call graph, without seeing the answer label, then scores them against the label.

Every rejection comes with a certificate: replay numbers, a timing contradiction, a retry-amplification ratio, an impossible edge, or a metric signature that contradicts the claimed fault.

## Run it

Python 3.10 or newer. The core needs only the standard library.

```bash
python3 -m rootproof evaluate     # re-verify every saved answer; rebuilds results/ and docs/ (about 40 s, offline)
open results/report.html          # the results site
```

Jev responses are cached in `results/jev_cache.json`, so `evaluate` needs no keys.

To ask the models again, put `OPENAI=...` and `TYPESAFE=...` in `.env` and run:

```bash
python3 -m rootproof ask            # simulated incidents, 5 runs each
python3 -m rootproof ask-rcaeval    # RCAEval cases
```

To rebuild the RCAEval packets from the raw benchmark (needs pandas and pyarrow), download the cases into `data/rcaeval/` from `huggingface.co/datasets/phamquiluan/RCAEval`, then run:

```bash
python3 -m rootproof prepare-rcaeval data/rcaeval
python3 -m rootproof rcaeval-traces
```

`python3 -m rootproof prompt 02` prints the exact prompt for one incident.

## Layout

```
rootproof/
  config.py          paths, model ids, replay constants
  clients.py         OpenAI (subject AI) and TypeSafe Jev clients, with the Jev cache
  harness.py         freezes incident prompts and records the subject AI's answers
  packets.py         renders an incident into the on-call packet
  pipeline.py        runs the whole evaluation and writes every artefact
  labels.py          reference, trap and audit labels that test the verifier itself
  sim/               replay engine (engine.py) and illustrative request traces (traces.py)
  scenario/          Larkwave topology, the 21-action catalogue, the 15 incidents (typed objects)
  verify/            extract (Jev), replay (decides), claims (explain), chain (causal graph), verdict
  rcaeval/           topology, packet preparation, claim checks, scoring with bootstrap intervals, traces
  report/            site data (data.py) and the offline HTML build (build.py, assets/)
tasks/               frozen prompts: simulated incidents and RCAEval cases (the evidence)
attempts/            the subject AI's unedited answers, with model, timestamp and prompt hash
results/             summary.json, report.html, jev_cache.json
docs/                ground_truth.md, WRITEUP.md, DEMO.md
tests/               regression gate and golden snapshot
```

## Results

Subject model: gpt-6-luna. Extraction: TypeSafe Jev.

**Simulated incidents** (15 fictional incidents, 5 runs each)

| | |
|---|---|
| First attempts accepted | 12 of 15 |
| All runs accepted | 58 of 75 |
| Runs that agree with their majority verdict | 92% on average (11 of 15 tasks identical in all 5 runs) |
| Fixes that hold on all five simulator seeds (accepted first attempts) | 11 of 12 |
| Verifier self-test (correct answers accepted, traps caught) | 30 of 30 |
| Jev extraction decisions matching hand labels | 313 of 315 |
| Causal-chain edges: precision / recall | 97% / 65% |

**Real telemetry** (RCAEval RE2, Online Boutique; 30 cases per instance)

| Split | Verifier agrees with label (95% CI) | AI right on service and fault | Notes |
|---|---|---|---|
| Dev (instance 2) | 77% (60–90%) | 53% | used to design the rules |
| Tuned (instance 1) | 87% (73–97%) | 70% | rules were tuned while looking at this split |
| **Fresh (instance 3)** | **90% (77–100%)** | **60%** | never looked at while tuning |

The AI always names the right service (97–100%). The weak spot is the fault type: it calls memory, socket and loss faults "delay". On the fresh split the verifier flags 11 answers and 91% of those flags are real errors.

**Findings**
1. **It fixes the trigger and misses the feedback loop.** When one config change is visibly the trigger, it reverts the change and stops. Layered retries are already sending about 8x load, so in replay errors stay at 100%. Tasks 02 and 13 fail in every run.
2. **Right fix, wrong story.** In task 15 (a loud retry storm hiding a quiet credential fault), the actions work but the named cause is contradicted: users saw errors 30 seconds before the blamed service degraded.
3. **The verifier's rules hold up on untouched data.** Accuracy on the fresh split (90%) is no lower than on the split the rules were tuned on (87%).

## Limitations

- The simulated systems are a fluid model, not a cluster. All names are fictional, and the dynamics are standard (queueing, timeouts, metastable retry storms).
- Real data cannot be replayed, so the RCAEval track checks claims, not fixes.
- One subject model. Tasks 12–15 were written after the first results, to test the hypotheses. Confirmed and refuted cases are both reported (09 and 10 pass).
- The RCAEval trace export records no error status, so trace views show call counts and latency only.
- Actions outside the 21-action catalogue are ignored. That can under-credit an unusual but valid fix; the extraction audit measures how often this happens.
