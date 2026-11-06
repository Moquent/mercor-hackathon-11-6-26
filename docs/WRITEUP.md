# Write-up (paste into the submission portal)

**RootProof checks an AI's incident root-cause analysis by replaying the outage with the AI's fix applied, instead of judging the prose.**

**Simulated incidents.** 15 seeded incidents on a fictional streaming backend: retries, timeouts, fallback chains, rate limits, multi-fault cases and a phantom outage. The subject AI (gpt-6-luna, a fresh request per run, five runs per task) answers from an on-call packet. TypeSafe Jev maps its prose onto 21 typed actions and its claimed causal edges. The actions are replayed from the page time, and an answer is accepted only if user errors fall below 5% and a named root cause passes the timing, reachability, origin-versus-victim and baseline checks. Ground truth is computed by replaying every action and pair of actions. Results: 12 of 15 first attempts accepted, 58 of 75 runs accepted, 92% of runs agree with their majority verdict, 11 of 12 accepted fixes hold on all five simulator seeds, 30 of 30 on the verifier self-test, and 313 of 315 extraction decisions match hand labels.

**Real telemetry.** RCAEval RE2 on Online Boutique: 30 cases per instance. The verifier checks the AI's claim without seeing the label: does the blamed service show an anomaly, does its metric signature match the claimed fault, did it degrade first, and is the claimed path a real call chain? On the fresh instance, which the rules never saw, it agrees with the label 90% of the time (95% interval 77–100%), and 91% of its flags are real errors. The tuned instance scores 87%, so the rules are not overfitted.

**Findings.**
1. The model fixes the trigger and misses the feedback loop. After a config change it reverts the change, but retries are already driving about 8x load, so errors stay at 100% in replay. Tasks 02 and 13 fail in every run.
2. Right fix, wrong story: in task 15 the actions work, but the blamed service degraded 30 seconds after users saw errors.
3. Its causal chains are precise (97%) but miss the load edges (65% recall).
4. On real data it always finds the service (97–100%) but mislabels the fault type about 40% of the time, mostly as "delay".
