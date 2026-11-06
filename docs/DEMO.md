# 3-minute demo script

Open `results/report.html` on the Overview tab.

1. **(0:00) Hook.** "AI on-call agents write confident root-cause analyses. Reading one can't tell you if it's right. RootProof replays the outage
   with the AI's fix applied, checks the causal chain it claims, and runs the same checks on real benchmark telemetry."
2. **(0:20) One task.** Incidents tab → **13 cache-pool**. "Someone cut media-cache's connection pool. The change log says so, and errors are at 100%."
   Click **What the AI saw** for a second, then **AI answer**: confident, right component, one action: "revert max_connections to 512."
3. **(0:50) What RootProof caught.** **Replay** tab: the orange line (AI's fix replayed) stays at 100%; the blue line (computed fix) drops to 0.
   Read the certificate: "media-cache is receiving 8.3x its normal request rate from layered retries."
4. **(1:15) Why.** **Causal graph** tab, Overlay mode: "The animated blue edges are the retry loop between player-core and media-cache. The AI's
   dashed chain describes the failure path but not the load loop, and that's the edge its fix ignores."
5. **(1:40) Not a rejection machine.** Overview heatmap: 12/15 accepted, including a red-herring deploy, a phantom outage and a two-fault
   incident. Self-test 30/30.
6. **(2:00) Real data.** Real data tab: on 30 held-out RCAEval cases, the AI always finds the service (100%, against a 73% baseline) but gets the fault type
   wrong 30% of the time. Without seeing labels, the verifier agrees with them 87% of the time, and 6 of its 7 flags are real errors. Click the checkout/mem
   row: "it said delay, but checkout's own memory spiked, and network delay doesn't do that."
7. **(2:35) Honest limits.** "The incident simulation is a fluid model, real data can't be replayed, there's one subject model, and the action catalog is fixed. Jev is audited
   at 313/315. Next: replay on Kubernetes with real fault injection."

Q&A prep: *Why simulate?* Testing a fix needs executable ground truth, and real outages can't be replayed. *Did you tune to the test set?* RCAEval rules were
designed on instance 2 and reported on instance 1. *Is the trap fair?* The packet shows the retry config and the 8x request rate, and the same model solved three
other retry-storm tasks.
