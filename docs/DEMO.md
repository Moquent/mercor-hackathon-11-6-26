# 3-minute demo script

Open `results/report.html` (one offline file). Start on Overview.

1. **(0:00) Problem.** "AI on-call agents write confident root-cause analyses, and reading one can't tell if it's right. RootProof replays the outage with the AI's fix applied, and checks the causal chain it claims."
2. **(0:20) Overview.** Point at the four numbers (and the Investigations row: 447 of 455 claims verified against the AI's own steps): 12 of 15 first answers accepted, 58 of 75 runs accepted, 97% of named causal steps are real, and on fresh real cases the AI names the right service and fault only 60% of the time. Then the verdict table: five runs per task, and 11 of 15 tasks give the same verdict every time.
3. **(0:50) One incident.** Incidents, then task 02 (persistent capacity cut). "The change log says the CPU limit was cut. The AI reverts it and stops. It's confident, and it's wrong."
4. **(1:10) Replay.** The chart: the AI's fix stays at 100% errors. The computed fix drops to 0. "Layered retries are already sending 8x the load, so reverting the cap alone doesn't recover."
5. **(1:30) The graph.** Click **media-cache**. The inspector shows the injected fault, its error and load curves, its log and its event. Then click the claimed edge in the overlay: the AI's claimed step is marked, and the missing load edge is shown.
6. **(1:45) Investigations.** Open Investigations, then 16-link-partition, run 1. "Here the AI had tools, and every step is recorded: what it called, why, and what it saw. The fix is right, but at step s15 it claims media-cache's failures reach player-core. It never probed the link. Here's the probe it skipped: 92% of connections refused. media-cache was healthy." Click s15 in the claims table to jump to the step. Then run 2: it probed the link, and every claim is verified.
7. **(2:10) Real data.** Real data tab. On 30 fresh RCAEval cases, the verifier agrees with the label 90% of the time (interval 77–100%), against 87% on the instance the rules were tuned on. Open a case: the minute-level metrics, the error logs, and the real call statistics per edge. "It says delay, but the service's memory spiked, and network delay doesn't do that."
8. **(2:40) Limits.** "The incident simulation is a fluid model. Real data can't be replayed, the trace export has no error status, and there's one subject model. Next: replay on Kubernetes with fault injection."

Q&A: *Why simulate?* Testing a fix needs executable ground truth, and real outages can't be replayed. *Were the rules tuned on the test set?* Yes, on instance 1, which is why the fresh instance 3 is reported separately, and it scores at least as well. *Is the trap fair?* The packet shows the retry config and the 8x request rate, and the same model solved three other retry-storm tasks.
