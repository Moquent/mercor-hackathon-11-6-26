# Write-up (paste into the submission portal)

**RootProof checks an AI's incident root-cause analysis by replaying the outage with the AI's fix applied, instead of judging the prose.**

**Simulated incidents.** 15 seeded incidents on a fictional streaming backend: retries, timeouts, fallback chains, rate limits, multi-fault
cases and a phantom outage. The subject AI (gpt-6-luna, a fresh request per task, 3 runs) answers from an on-call packet. TypeSafe Jev maps
its prose onto 21 typed actions and its claimed causal edges. The actions are replayed from page time, and the answer is accepted only if user
errors fall below 5% **and** a named root cause passes the timing, reachability, origin-vs-victim and baseline checks. Ground truth is computed by
replaying every action and pair of actions, so any working fix is accepted. Results: 12/15 first attempts accepted, 30/30 on the verifier
self-test, and 313/315 extraction decisions matching hand labels.

**Real telemetry.** 60 RCAEval RE2 cases on Online Boutique (30 dev, 30 held out). The verifier checks the AI's claim without seeing the label:
does the blamed service show an anomaly, does its metric signature match the claimed fault type, did it degrade first, and is the claimed path
a real call chain? On the held-out split it agrees with the label 87% of the time, and 6 of its 7 flags are real errors.

**Findings.** (1) The model fixes the trigger and misses the feedback loop: after a config change, it reverts it, but retries are already
driving ~8x load, so errors stay at 100% in replay (02 and 13 in every run). (2) Right fix, wrong story: in 15 its actions work, but the blamed
service degraded 30 s after users saw errors. (3) The AI's causal chains are precise (97%) but miss load edges (65% recall). (4) On real data it
always finds the service (100%, against 73% for a z-score baseline) but mislabels the fault type 30% of the time, mostly as "delay".
