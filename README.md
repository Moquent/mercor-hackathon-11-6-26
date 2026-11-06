# RootProof

An AI on-call agent writes a confident root-cause analysis. RootProof checks it without trusting the prose.

1. **Replay.** It replays a seeded incident with the AI's proposed fix applied. The fix counts only if users stop seeing errors, and the result is re-checked under five simulator seeds.
2. **Causal chain.** It compares the propagation chain the AI claims with the true one from the simulation.
3. **Investigation trail.** The AI investigates with tools (metrics, logs, health checks, connectivity probes, change log). Every call is recorded with its arguments, the reason the AI gave, the output it saw and the model's reasoning summary. Every claim in the final answer must cite the steps that show it. RootProof then checks each claim three ways: did the AI look, does the cited step show it, and is it true. It also reports the checks the AI never made and the first step where the trail went wrong.
4. **Real telemetry.** On RCAEval (real fault injections on Online Boutique) it checks the AI's claims against the metrics and the call graph, without seeing the answer label, then scores them against the label.

Every rejection comes with a certificate: replay numbers, a timing contradiction, a retry-amplification ratio, an impossible edge, or a metric signature that contradicts the claimed fault.

## Live console

```bash
python3 -m rootproof serve        # http://127.0.0.1:8080/
```

One screen that runs everything while you watch, streamed over Server-Sent Events:
- **Topology:** the simulated system animates second by second, with request particles, error colouring and broken links. An agent cursor moves to whatever service or link the AI is inspecting.
- **Agent stream:** every tool call with the AI's stated reason, the reasoning summary, the output, and the token count and latency of each exchange. You can replay a saved run (offline, free) or run the agent live (real API calls).
- **Verification pipeline:** extraction, replay on five seeds (curves drawn as they finish), story checks, each claim's three checks, the checks it skipped, and the first wrong step.
- **Benchmark:** re-verifies all saved investigations, filling a grid and live totals.
- **Case composer:** describe an incident in plain words. The AI drafts it as a case, the server validates and simulates it, computes the ground truth, and the agent can investigate it. Drafted cases are saved in `cases/custom/`.
- **Event log:** every event as JSON, filterable. The API and event schema are in `rootproof/live/EVENTS.md`.

In Docker (isolated; the console binds to localhost only):

```bash
colima start                      # or Docker Desktop
docker compose up console         # the console
docker compose run --rm bench     # the full offline evaluation
docker compose run --rm gate      # the regression gate
```

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
python3 -m rootproof investigate    # tool-using investigations, 3 runs each, every step recorded
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
  audit_extra.py     second hand audit of the extraction, on a seeded sample of runs 2-5
  investigation.py   evaluates the recorded investigations and self-tests the trace verifier
  agent/             the investigation tools (tools.py) and the recorded tool-calling loop (run.py)
  live/              the console: server (stdlib HTTP + SSE), event bus, streamed jobs, case composer, assets/
  sim/               replay engine (engine.py), including network link faults
  scenario/          Larkwave topology, the 21-action catalogue, the 15 incidents (typed objects)
  verify/            extract (Jev), replay (decides), claims (explain), chain (causal graph), verdict, trace (claim vs steps)
  rcaeval/           topology, packet preparation, claim checks, scoring with bootstrap intervals, traces
  report/            site data (data.py) and the offline HTML build (build.py, assets/)
tasks/               frozen prompts: simulated incidents and RCAEval cases (the evidence)
attempts/            the subject AI's unedited answers, with model, timestamp and prompt hash
  agent/             full investigation records: every request, raw response, step, reason and reasoning summary
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
| Causal-chain edges: precision / recall | 97% / 58% |
| Second extraction audit (20 answers from runs 2–5) | 418 of 420 decisions; 18 of 20 root lists |

**Investigations** (17 incidents including two network partitions, 3 runs each, 51 recorded investigations, 22 tool calls on average)

| | |
|---|---|
| First attempts accepted (replay + story) | 13 of 17 (11 of the 15 shared incidents, against 12 without tools) |
| Claims checked against the AI's own steps | 455, of which 447 verified, 7 false, 1 unsupported, 0 ungrounded |
| Runs with a working fix but a flawed trail | 5 of 51 |
| Runs that skipped a check on the true causal path | 16 of 51 |
| Trace-verifier self-test (hand-built traces) | 3 of 3 |

**Real telemetry** (RCAEval RE2, Online Boutique; 30 cases per instance)

| Split | Verifier agrees with label (95% CI) | AI right on service and fault | Notes |
|---|---|---|---|
| Dev (instance 2) | 77% (60–90%) | 53% | used to design the rules |
| Tuned (instance 1) | 87% (73–97%) | 70% | rules were tuned while looking at this split |
| **Fresh (instance 3)** | **90% (77–100%)** | **60%** | never looked at while tuning |

The AI always names the right service (97–100%). The weak spot is the fault type: it calls memory, socket and loss faults "delay". On the fresh split the verifier flags 11 answers and 91% of those flags are real errors.

**Findings**
1. **The fix can be right while the reasoning is wrong.** In incident 16 a NetworkPolicy change blocks player-core from reaching a healthy media-cache. In run 1 the AI reverts the policy (the fix works) but never probes the connection. Its trail says "media-cache failures reach player-core", which is false: media-cache had 0% errors. The verifier pins the error to step s15 and shows the check that would have caught it (`check_connectivity(player-core, media-cache)` returns 92% refused). Runs 2 and 3 made that probe and their trails are clean.
2. **It fixes the trigger and misses the feedback loop.** When one config change is visibly the trigger, it reverts the change and stops. Layered retries are already sending about 8x load, so in replay errors stay at 100%. Without tools, tasks 02 and 13 fail in every run. With tools, 02 passes in all three runs, but 13 still fails in all three.
3. **Right fix, wrong story.** In task 15 (a loud retry storm hiding a quiet credential fault), the actions work but the named cause is contradicted: users saw errors 30 seconds before the blamed service degraded.
4. **The verifier's rules hold up on untouched data.** Accuracy on the fresh split (90%) is no lower than on the split the rules were tuned on (87%).

## Limitations
- The investigation track records everything the API returns. OpenAI does not expose raw chain of thought, only reasoning summaries, which are stored per step. Each tool call also carries the AI's own one-sentence reason.
- Claims are the AI's own typed statements with cited step ids. A claim it never wrote cannot be checked; missed checks cover that gap only for the true causal path.
- The trace verifier's rules were corrected once after reading the first outputs. Pre-existing errors now count as "erroring". Fallback load and physical failure edges at page time count as true. "No root cause" is true for the phantom-outage incident. A connection-refused log line counts as evidence of an unreachable link. A media-cache with its own broken egress link is an origin, not a victim. The fallback fix also changed the packet track's causal-chain truth (recall 65% → 58%), with no verdict changes.

- The simulated systems are a fluid model, not a cluster. All names are fictional, and the dynamics are standard (queueing, timeouts, metastable retry storms).
- Real data cannot be replayed, so the RCAEval track checks claims, not fixes.
- One subject model. Tasks 12–15 were written after the first results, to test the hypotheses. Confirmed and refuted cases are both reported (09 and 10 pass).
- The RCAEval trace export records no error status, so trace views show call counts and latency only.
- Actions outside the 21-action catalogue are ignored. That can under-credit an unusual but valid fix; the extraction audit measures how often this happens.
