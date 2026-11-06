# DAGScope evaluation report

Generated from result files; every count below comes from an executed run.

## Verifier correctness

- Test suite (2026-11-06T18:38:00Z): 278 passed, 0 failed, 0 skipped.
- Planted-bug check: 16/16 deliberately broken verifier variants were caught by the offline tests.

## Model attempts

### openai/gpt-5-mini / chronological_only

Evaluated (UTC): 2026-11-06. First attempts; one fresh conversation per task.

| Measure | Result |
|---|---|
| Tasks fully correct | 10/12 |
| Claims accepted (SUPPORTED) | 30/32 |
| Claims rejected (CONTRADICTED or INVALID_CLAIM) | 0/32 |
| Claims inconclusive (NOT_OBSERVED) | 2/32 |
| False-positive reachability claims | 0/12 |
| False-negative reachability claims, complete traces | 0/10 |
| Incorrect certainty, incomplete traces | 2/4 |
| Format failures | 0/12 |

| Failure mode | Correct tasks | Incorrect | Format failures |
|---|---|---|---|
| partial_observability | 1/3 | 2 | 0 |
| retry_attribution | 3/3 | 0 | 0 |
| shared_ancestor | 3/3 | 0 | 0 |
| temporal_correlation | 3/3 | 0 | 0 |

Observed mismatches:

- `scenario_3_task_2`: A.created.message_published -> A.created.a1.order_state_committed: claimed reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)
- `scenario_3_task_3`: A.created.a1.idempotency_check -> A.created.a1.order_state_committed: claimed reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)

### openai/gpt-5-mini / explicit_parents

Evaluated (UTC): 2026-11-06. First attempts; one fresh conversation per task.

| Measure | Result |
|---|---|
| Tasks fully correct | 12/12 |
| Claims accepted (SUPPORTED) | 32/32 |
| Claims rejected (CONTRADICTED or INVALID_CLAIM) | 0/32 |
| Claims inconclusive (NOT_OBSERVED) | 0/32 |
| False-positive reachability claims | 0/10 |
| False-negative reachability claims, complete traces | 0/10 |
| Incorrect certainty, incomplete traces | 0/2 |
| Format failures | 0/12 |

| Failure mode | Correct tasks | Incorrect | Format failures |
|---|---|---|---|
| partial_observability | 3/3 | 0 | 0 |
| retry_attribution | 3/3 | 0 | 0 |
| shared_ancestor | 3/3 | 0 | 0 |
| temporal_correlation | 3/3 | 0 | 0 |

## Handcrafted fixtures (not model output)

- `HANDCRAFTED_correct__explicit_parents`: 12/12 tasks graded correct; verdicts {'SUPPORTED': 32, 'CONTRADICTED': 0, 'NOT_OBSERVED': 0, 'INVALID_CLAIM': 0}.
- `HANDCRAFTED_plausible_wrong__explicit_parents`: 0/12 tasks graded correct; verdicts {'SUPPORTED': 18, 'CONTRADICTED': 11, 'NOT_OBSERVED': 3, 'INVALID_CLAIM': 0}.

## External track: RCAEval real traces

Real traces from a third-party benchmark (Pham et al., WWW 2025), reported separately from the primary tasks.

**Verifier on real traces** (16 cases, 7,763,649 spans, 7,579,851 edges):

- Real gaps found: 126,236 dangling parent links across 10/16 cases; 0 cycles.
- NetworkX vs independent search: 0 disagreements over 32,000 exact descendant sets.
- Hiding 10% of spans, over 16,000 claim pairs: 0 false SUPPORTED, 0 wrongly CONTRADICTED, 0 negative claims accepted. 194 true paths became NOT_OBSERVED, as they should.
- False completeness claims on gapped traces: 0/10 failed to be revoked.

**openai/gpt-5-mini** (2026-11-06 UTC, first attempts):

| Measure | Result |
|---|---|
| Injected service identified (intervention label) | 10/16 |
| Same, when the error-blame baseline is wrong | 6/11 |
| Same, when the service reports no spans | 2/2 |
| Error-blame baseline (no model) | 5/16 |
| Reachability and gap tasks fully correct | 2/7 |
| Claims accepted / inconclusive / rejected | 14 / 10 / 0 of 24 |
| Incorrect certainty on partial traces | 10/17 |
| Format failures | 0/23 |

Observed misses:

- `rcaeval_attr_re2ob_currencyservice_delay_1`: claimed frontendservice, injected currencyservice (fault delay, stated confidence 0.85)
- `rcaeval_attr_re2ob_productcatalogservice_loss_1`: claimed recommendationservice, injected productcatalogservice (fault loss, stated confidence 0.9)
- `rcaeval_attr_re2tt_ts-order-service_delay_1`: claimed ts-preserve-service, injected ts-order-service (fault delay, stated confidence 0.85)
- `rcaeval_attr_re2tt_ts-route-service_disk_1`: claimed ts-auth-service, injected ts-route-service (fault disk, stated confidence 0.8)
- `rcaeval_attr_re2tt_ts-train-service_loss_1`: claimed ts-travel-service, injected ts-train-service (fault loss, stated confidence 0.7)
- `rcaeval_attr_re3ob_currencyservice_f1_1`: claimed checkoutservice, injected currencyservice (fault f1, stated confidence 0.95)
- `rcaeval_reach_re2ob_productcatalogservice_loss_1`: nearby_failure_other_trace -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED); sibling_span -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)
- `rcaeval_reach_re3ob_adservice_f3_1`: nearby_failure_other_trace -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED); sibling_span -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)
- `rcaeval_reach_re3ob_cartservice_f1_1`: nearby_failure_other_trace -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED); sibling_span -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)
- `rcaeval_reach_re3ob_currencyservice_f1_1`: nearby_failure_other_trace -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED); sibling_span -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)
- `rcaeval_reach_re3ob_emailservice_f1_1`: nearby_failure_other_trace -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED); sibling_span -> failing_span: claimed not_reachable, evidence supports insufficient_evidence (verdict NOT_OBSERVED)


These are small test cases: findings describe observed failure modes in this benchmark, not a model's general causal reasoning.
