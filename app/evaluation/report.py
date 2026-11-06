"""Generates benchmark/results/REPORT.md strictly from result files on disk."""
from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "benchmark" / "results"


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _ratio(metric: dict) -> str:
    return f"{metric['count']}/{metric['denominator']}"


def _external_section() -> list[str]:
    root = RESULTS.parent / "rcaeval" / "results"
    lines = ["", "## External track: RCAEval real traces", "",
             "Real traces from a third-party benchmark (Pham et al., WWW 2025), reported separately from the primary tasks.", ""]
    real = _load(root / "verifier_on_real_traces.json")
    if real:
        t = real["totals"]
        lines += [f"**Verifier on real traces** ({t['cases']} cases, {t['spans']:,} spans, {t['edges']:,} edges):", "",
                  f"- Real gaps found: {t['dangling_edges']:,} dangling parent links across {t['cases_with_gaps']}/{t['cases']} cases; {t['cycles']} cycles.",
                  f"- NetworkX vs independent search: {t['oracle_disagreements']} disagreements over {t['oracle_starts_checked']:,} exact descendant sets.",
                  f"- Hiding 10% of spans, over {t['masked_pairs']:,} claim pairs: {t['false_support']} false SUPPORTED, {t['wrongly_contradicted']} wrongly CONTRADICTED, {t['negative_accepted']} negative claims accepted. {t['true_paths_hidden_by_mask']:,} true paths became NOT_OBSERVED, as they should.",
                  f"- False completeness claims on gapped traces: {t['revocations_failed']}/{t['revocations_checked']} failed to be revoked.", ""]
    for path in sorted(root.glob("*/metrics.json")):
        m = _load(path)
        a, r = m["attribution"], m["reachability"]
        lines += [f"**{m['model_name']}** ({', '.join(m['evaluated_dates_utc'])} UTC, first attempts):", "",
                  "| Measure | Result |", "|---|---|",
                  f"| Injected service identified (intervention label) | {a['correct']}/{a['tasks']} |",
                  f"| Same, when the error-blame baseline is wrong | {a['correct_when_naive_wrong']}/{a['naive_wrong']} |",
                  f"| Same, when the service reports no spans | {a['correct_service_absent']}/{a['service_absent']} |",
                  f"| Error-blame baseline (no model) | {a['naive_correct']}/{a['tasks']} |",
                  f"| Reachability and gap tasks fully correct | {r['tasks_correct']}/{r['tasks_evaluated']} |",
                  f"| Claims accepted / inconclusive / rejected | {r['claims_accepted']} / {r['claims_inconclusive']} / {r['claims_rejected']} of {r['claims_total']} |",
                  f"| Incorrect certainty on partial traces | {r['incorrect_certainty_incomplete']['count']}/{r['incorrect_certainty_incomplete']['denominator']} |",
                  f"| Format failures | {a['format_failures'] + r['format_failures']}/{a['tasks'] + r['tasks_evaluated']} |", ""]
        if m["misses"]:
            lines += ["Observed misses:", ""] + [f"- `{x['task_id']}`: {x['detail']}" for x in m["misses"]] + [""]
    return lines


def write_report() -> Path:
    lines = ["# DAGScope evaluation report", "", "Generated from result files; every count below comes from an executed run.", ""]
    tests = _load(RESULTS / "verifier_tests.json")
    mutation = _load(RESULTS / "mutation_check.json")
    lines += ["## Verifier correctness", ""]
    if tests:
        lines.append(f"- Test suite ({tests['executed_at_utc'][:19]}Z): {tests['passed']} passed, {tests['failed']} failed, {tests['skipped']} skipped.")
    if mutation:
        lines.append(f"- Planted-bug check: {mutation['caught']}/{mutation['total']} deliberately broken verifier variants were caught by the offline tests.")
    lines.append("")
    runs = sorted(p.parent for p in RESULTS.glob("*/metrics.json"))
    real = [p for p in runs if not p.name.startswith("HANDCRAFTED")]
    lines += ["## Model attempts", ""]
    if not real:
        lines += ["No model attempts exist yet, so no AI performance is reported.", ""]
    for run in real:
        m = _load(run / "metrics.json")
        graded = _load(run / "per_task.json")
        lines += [
            f"### {m['model_name']} / {m['condition']}", "",
            f"Evaluated (UTC): {', '.join(m['evaluated_dates_utc'])}. First attempts; one fresh conversation per task.", "",
            "| Measure | Result |", "|---|---|",
            f"| Tasks fully correct | {m['tasks_correct']}/{m['tasks_evaluated']} |",
            f"| Claims accepted (SUPPORTED) | {m['claims_accepted']}/{m['claims_total']} |",
            f"| Claims rejected (CONTRADICTED or INVALID_CLAIM) | {m['claims_rejected']}/{m['claims_total']} |",
            f"| Claims inconclusive (NOT_OBSERVED) | {m['claims_inconclusive']}/{m['claims_total']} |",
            f"| False-positive reachability claims | {_ratio(m['false_positive_reachability'])} |",
            f"| False-negative reachability claims, complete traces | {_ratio(m['false_negative_reachability_complete'])} |",
            f"| Incorrect certainty, incomplete traces | {_ratio(m['incorrect_certainty_incomplete'])} |",
            f"| Format failures | {m['format_failures']}/{m['tasks_evaluated']} |", "",
            "| Failure mode | Correct tasks | Incorrect | Format failures |", "|---|---|---|---|",
        ]
        for category, row in sorted(m["error_categories"].items()):
            lines.append(f"| {category} | {row['correct']}/{row['tasks']} | {row['incorrect']} | {row['format_failures']} |")
        lines.append("")
        misses = [g for g in graded if not g["task_correct"]]
        if misses:
            lines += ["Observed mismatches:", ""] + [f"- `{g['task_id']}`: {g['mismatch']}" for g in misses] + [""]
    lines += ["## Handcrafted fixtures (not model output)", ""]
    for run in runs:
        if run.name.startswith("HANDCRAFTED"):
            m = _load(run / "metrics.json")
            lines.append(f"- `{run.name}`: {m['tasks_correct']}/{m['tasks_evaluated']} tasks graded correct; verdicts {m['verdict_counts']}.")
    lines += _external_section()
    lines += ["", "These are small test cases: findings describe observed failure modes in this benchmark, not a model's general causal reasoning.", ""]
    path = RESULTS / "REPORT.md"
    path.write_text("\n".join(lines))
    return path


if __name__ == "__main__":
    print(write_report())
