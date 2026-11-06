"""DAGScope dashboard. Reads benchmark artifacts from disk, so it works without credentials or a live stack."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import pandas as pd
import streamlit as st

from app.config import GATEWAY_URL
from app.evaluation.harness import BENCH, load_artifact, load_public_tasks, load_truth
from app.evaluation.model_client import ModelConfig, complete_once
from app.evaluation.prompts import build_prompt
from app.evaluation.schema import parse_answer
from app.evaluation.scoring import task_view
from app.scenarios.ground_truth import apply_mask
from app.verifier.claims import EVALUABLE, verify_claim
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag

st.set_page_config(page_title="DAGScope", layout="wide")

SERVICE_COLORS = {"gateway": "#dbeafe", "orders": "#dcfce7", "inventory": "#fef3c7", "worker": "#ede9fe"}
VERDICT_ICON = {"SUPPORTED": "✅", "CONTRADICTED": "❌", "NOT_OBSERVED": "❔", "INVALID_CLAIM": "🚫"}
SCENARIO_TITLES = {
    "scenario_1": "1 · Temporal correlation trap", "scenario_2": "2 · Duplicate message & retry trap",
    "scenario_3": "3 · Partial observability trap", "scenario_4": "4 · Shared ancestor trap",
}
# The trap each scenario sets, pre-selected so the graph opens on the interesting claim.
DEFAULT_CLAIM = {
    "scenario_1": ("B.created.a1.dependency_warning", "A.created.a1.payment_failed"),
    "scenario_2": ("A.created.a1.message_delivered", "A.created.a2.order_state_committed"),
    "scenario_3": ("A.order_created", "A.created.a1.order_state_committed"),
    "scenario_4": ("A.reserve.a1.reservation_failed", "A.charge.a1.payment_failed"),
}


@st.cache_data
def artifact(scenario_id: str, directory: str = "runs") -> dict[str, Any]:
    return json.loads((BENCH / directory / f"{scenario_id}.json").read_text())


def benchmark_ready() -> bool:
    return (BENCH / "runs" / "scenario_1.json").exists() and (BENCH / "tasks" / "scenario_1_task_1.json").exists()


def result_sets() -> dict[str, Path]:
    return {p.parent.name: p.parent for p in sorted((BENCH / "results").glob("*/metrics.json"))}


def reverse_names(art: dict[str, Any]) -> dict[str, str]:
    return {v: k for k, v in art["manifest"].items()}


def dot_for(events: list[dict], deps: list[dict], names: dict[str, str], *, source: str | None = None, target: str | None = None,
            path: list[str] | None = None, second_path: list[str] | None = None, hidden_events: set[str] | None = None,
            hidden_edges: set[tuple[str, str]] | None = None) -> str:
    """Graphviz DOT, rendered in the browser. One cluster per request trace; nothing inferred from time."""
    hidden_events, hidden_edges = hidden_events or set(), hidden_edges or set()
    path_edges = set(zip(path or [], (path or [])[1:])) | set(zip(second_path or [], (second_path or [])[1:]))
    path_nodes = set(path or []) | set(second_path or [])
    lines = ['digraph G {', 'rankdir=TB; nodesep=0.25; ranksep=0.32; fontname="Helvetica"; bgcolor="transparent";',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 margin="0.08,0.04"];',
             'edge [color="#94a3b8" arrowsize=0.6 fontname="Helvetica" fontsize=8];']
    by_trace: dict[str, list[dict]] = {}
    for e in events:
        by_trace.setdefault(e["trace_id"], []).append(e)
    for i, (trace, rows) in enumerate(by_trace.items()):
        label = rows[0]["metadata"].get("label", "")
        lines.append(f'subgraph cluster_{i} {{ label="request {label}"; color="#cbd5e1"; fontsize=11; style="rounded";')
        for e in rows:
            eid = e["event_id"]
            fill = SERVICE_COLORS.get(e["service"], "#f1f5f9")
            border, width, style = "#64748b", 1, "rounded,filled"
            if e["status"] in ("failed", "rolled_back"):
                border, width = "#dc2626", 2
            elif e["status"] == "committed":
                border, width = "#15803d", 2
            elif e["status"] == "warning":
                border, width = "#d97706", 2
            if eid in hidden_events:
                fill, style, border, width = "#f8fafc", "rounded,filled,dashed", "#64748b", 1
            if eid in path_nodes:
                width = 3
            if eid in (source, target):
                border, width = "#2563eb", 4
            text = f"{e['event_type']}\\n{e['service']} · {e['status']} · {eid[:8]}"
            lines.append(f'"{eid}" [label="{text}" fillcolor="{fill}" color="{border}" penwidth={width} style="{style}"];')
        lines.append("}")
    seen: set[tuple[str, str]] = set()
    for d in deps:
        pair = (d["parent_event_id"], d["child_event_id"])
        if pair in seen:
            continue
        seen.add(pair)
        attrs = []
        if d["dependency_type"] == "retry_dependency":
            attrs += ['label="retry"', 'fontcolor="#b91c1c"']
        elif d["dependency_type"] == "database_commit":
            attrs += ['label="db read"', 'fontcolor="#15803d"']
        if pair in hidden_edges:
            attrs += ['style=dashed', 'color="#dc2626"', 'label="hidden from AI"', 'fontcolor="#dc2626"']
        elif pair in path_edges:
            attrs += ['color="#2563eb"', 'penwidth=3']
        lines.append(f'"{pair[0]}" -> "{pair[1]}" [{" ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def legend() -> None:
    st.caption("Fill = service: 🟦 gateway · 🟩 orders · 🟨 inventory · 🟪 worker. Border: green committed, red failed/rolled back, amber warning, "
               "thick blue claimed source/target. Blue edges: verified path. Dashed: not visible to the AI (private graph only).")


def named_path(path: list[str], names: dict[str, str]) -> str:
    return " → ".join(f"`{names.get(p, p[:8])}`" for p in path)


def render_verdict(result: dict[str, Any], names: dict[str, str]) -> None:
    claim = result["claim"]
    src = names.get(claim.get("source"), claim.get("source"))
    tgt = names.get(claim.get("target"), claim.get("target"))
    st.markdown(f"{VERDICT_ICON[result['verdict']]} **{result['verdict']}** · `{src}` **{claim.get('relationship')}** `{tgt}`")
    st.write(result["explanation"])
    if result["dependency_path"]:
        st.markdown("Path: " + named_path(result["dependency_path"], names))
    if result.get("second_path"):
        st.markdown("Second path: " + named_path(result["second_path"], names))


def source_banner(metrics: dict[str, Any]) -> None:
    if metrics.get("source") == "handcrafted":
        st.warning(f"**{metrics.get('fixture_label')}.** Written by hand to exercise the checker. Says nothing about any AI model.")
    else:
        st.success(f"Real first attempts from **{metrics.get('model_name')}** ({metrics.get('condition')}), evaluated {', '.join(metrics.get('evaluated_dates_utc', []))} UTC.")


if not benchmark_ready():
    st.error("No captured benchmark found. Run `make scenarios tasks` against the running stack.")
    st.stop()

st.title("DAGScope")
st.caption("Causal reachability verification: AI explanations of distributed-system incidents, checked against recorded execution dependencies.")

results = result_sets()
real_sets = [k for k in results if not k.startswith("HANDCRAFTED")]
# Without real attempts, open on the wrong-answer fixture: it is the one that shows the checker rejecting something.
default = next((k for k in real_sets if "explicit_parents" in k), real_sets[0]) if real_sets else next((k for k in results if "plausible_wrong" in k), next(iter(results), None))
with st.sidebar:
    st.header("Evidence set")
    selected = st.selectbox("Attempts to display", list(results), index=list(results).index(default) if default else None,
                            help="Real model attempts, or handcrafted fixtures that are labelled as such.") if results else None
    if not real_sets:
        st.info("No real model attempts yet. Only handcrafted fixtures are available.")

metrics = json.loads((results[selected] / "metrics.json").read_text()) if selected else None
graded = {g["task_id"]: g for g in json.loads((results[selected] / "per_task.json").read_text())} if selected else {}
attempts_dir = BENCH / "model_attempts" / selected if selected else None
tasks = {t["task_id"]: t for t in load_public_tasks()}

overview, dag, ai_vs_truth, runner, experiments, real_traces = st.tabs(["Overview", "Execution DAG", "AI vs Ground Truth", "Scenario Runner", "Experimental Results", "Real Traces (RCAEval)"])

with overview:
    if metrics:
        source_banner(metrics)
        cols = st.columns(5)
        cols[0].metric("Tasks evaluated", metrics["tasks_evaluated"])
        cols[1].metric("Accepted claims", metrics["claims_accepted"])
        cols[2].metric("Rejected claims", metrics["claims_rejected"])
        cols[3].metric("Inconclusive claims", metrics["claims_inconclusive"])
        cols[4].metric("Format failures", metrics["format_failures"])
    else:
        st.info("No attempts have been scored yet.")
    st.subheader("What the verdicts mean")
    st.markdown("""
| Verdict | Meaning |
|---|---|
| ✅ SUPPORTED | The recorded evidence establishes the claim, and the path is shown. |
| ❌ CONTRADICTED | The claim conflicts with evidence that is sufficiently complete to decide it. |
| ❔ NOT_OBSERVED | No path is visible, but the trace is incomplete, so nothing is concluded. |
| 🚫 INVALID_CLAIM | Malformed, refers to unavailable ids, or asks something the checker can't evaluate (such as "root cause"). |

A path proves **recorded dependency reachability**, not counterfactual causation. Causal statements come only from intervention runs.
""")
    st.subheader("Captured executions")
    rows = []
    for sid, title in SCENARIO_TITLES.items():
        a = artifact(sid)
        rows.append({"Scenario": title, "Run": a["run_id"], "Events": a["integrity"]["event_count"], "Edges": a["integrity"]["edge_count"],
                     "Services": ", ".join(sorted({e["service"] for e in a["events"]})), "Integrity valid": a["integrity"]["valid"], "Closed-world": a["coverage"]["closed_world"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

with dag:
    c1, c2 = st.columns([1, 2])
    sid = c1.selectbox("Scenario", list(SCENARIO_TITLES), format_func=SCENARIO_TITLES.get, key="dag_s")
    art = artifact(sid)
    names = reverse_names(art)
    scenario_tasks = [t for t in tasks if tasks[t]["scenario_id"] == sid]
    view_choice = c2.selectbox("Evidence view", ["Complete private graph"] + scenario_tasks, key="dag_view",
                               help="Pick a task to judge claims against exactly what the AI saw, with that task's coverage.")
    if view_choice == "Complete private graph":
        coverage = CoverageMetadata.model_validate(art["coverage"])
        vis_events, vis_deps = art["events"], art["dependencies"]
        hidden_ev, hidden_ed = set(), set()
    else:
        truth = load_truth(view_choice)
        _, coverage = task_view(art, truth)
        vis_events, vis_deps, hidden = apply_mask(art, truth["ground_truth"]["mask"])
        hidden_ev = set(hidden)
        visible_pairs = {(d["parent_event_id"], d["child_event_id"]) for d in vis_deps}
        hidden_ed = {(d["parent_event_id"], d["child_event_id"]) for d in art["dependencies"]} - visible_pairs
    graph = build_dependency_dag(vis_events, vis_deps, coverage)
    st.caption(f"Coverage: **{coverage.level}**, closed-world: **{coverage.closed_world}**. {coverage.notes}")

    st.markdown("**Verify a claim** (evaluated live by the deterministic checker)")
    ids = [e["event_id"] for e in vis_events]
    fmt = lambda eid: names.get(eid, eid[:8])
    src_name, tgt_name = DEFAULT_CLAIM[sid]
    default_src = ids.index(art["manifest"][src_name]) if art["manifest"][src_name] in ids else 0
    default_tgt = ids.index(art["manifest"][tgt_name]) if art["manifest"][tgt_name] in ids else len(ids) - 1
    a1, a2, a3 = st.columns(3)
    src = a1.selectbox("Source", ids, index=default_src, format_func=fmt, key=f"src_{sid}_{view_choice}")
    tgt = a2.selectbox("Target", ids, index=default_tgt, format_func=fmt, key=f"tgt_{sid}_{view_choice}")
    rel = a3.selectbox("Relationship", list(EVALUABLE) + ["caused"], key=f"rel_{sid}_{view_choice}")
    anc = None
    if rel in ("shared_ancestor", "no_shared_ancestor"):
        anc = st.selectbox("Ancestor", [None] + ids, format_func=lambda x: "(any)" if x is None else fmt(x), key=f"anc_{sid}_{view_choice}")
    result = verify_claim({"source_event_id": src, "target_event_id": tgt, "relationship": rel, "ancestor_event_id": anc}, graph, coverage)
    render_verdict(result, names)
    with st.expander("Structured verifier output (JSON)"):
        st.json(result)
    legend()
    dot_events = art["events"] if view_choice != "Complete private graph" else vis_events
    dot_deps = art["dependencies"] if view_choice != "Complete private graph" else vis_deps
    st.graphviz_chart(dot_for(dot_events, dot_deps, names, source=src, target=tgt, path=result["dependency_path"], second_path=result.get("second_path"),
                              hidden_events=hidden_ev, hidden_edges=hidden_ed), use_container_width=True)
    if art.get("interventions"):
        st.subheader("Intervention runs (separate causal evidence)")
        st.caption("Each is a separate real execution with one factor changed. Faults are injected and labelled, so these show what this controlled system does, not a production root cause.")
        st.dataframe(pd.DataFrame([{"Intervention": i["name"], "Change": i["description"], "Failure events": ", ".join(i["failure_events"]) or "none",
                                    "Order outcomes": json.dumps(i["order_outcomes"])} for i in art["interventions"]]), hide_index=True, use_container_width=True)

with ai_vs_truth:
    if not selected:
        st.info("No attempts have been scored yet.")
    else:
        source_banner(metrics)
        task_id = st.selectbox("Task", sorted(graded), key="cmp_task")
        task, truth, g = tasks[task_id], load_truth(task_id), graded[task_id]
        art = artifact(task["scenario_id"])
        names = reverse_names(art)
        attempt = json.loads((attempts_dir / f"{task_id}.json").read_text())
        st.markdown(f"**Question.** {task['question']}")
        st.caption(f"Coverage shown to the AI: **{task['coverage']}** · difficulty: {task['difficulty']}")
        st.markdown(("### ✅ Fully correct" if g["task_correct"] else "### ❌ Mismatch") + ("" if g["task_correct"] else f"\n\n{g['mismatch']}"))
        left, right = st.columns(2)
        with left:
            st.subheader("AI's first answer" if attempt.get("source") == "model" else "Handcrafted answer")
            if attempt.get("parsed"):
                st.markdown(f"*{attempt['parsed']['explanation']}*")
                st.caption(f"Stated confidence: {attempt['parsed']['confidence']}")
            else:
                st.error(f"Format failure: {attempt.get('parse_error')}")
            for c in g["claims"]:
                render_verdict(c["result"], names)
                st.divider()
            with st.expander("Raw response, verbatim"):
                st.code(attempt.get("raw_response") or "", language="json")
        with right:
            st.subheader("Independently verified answer")
            st.caption(truth["ground_truth"]["computed_by"])
            for slot in truth["ground_truth"]["slots"]:
                st.markdown(f"`{slot['source_name']}` **{slot['relationship']}** `{slot['target_name']}`")
                if slot.get("path"):
                    st.caption("Path: " + " → ".join(names.get(p, p[:8]) for p in slot["path"]))
                if slot.get("acceptable_ancestors"):
                    st.caption("Acceptable ancestors: " + ", ".join(names.get(p, p[:8]) for p in slot["acceptable_ancestors"]))
                if task["coverage"] == "partial":
                    st.caption(f"Reachable in the private complete graph: {slot['reachable_in_complete_private_graph']} (not provable from the export alone unless a path is listed).")
        options = {f"{c['raw']['relationship']}: {names.get(c['raw']['source_event_id'], '?')} → {names.get(c['raw']['target_event_id'], '?')}": c for c in g["claims"]}
        if options:
            chosen = options[st.selectbox("Claim to highlight on the graph", list(options))]
            events, deps, hidden = apply_mask(art, truth["ground_truth"]["mask"])
            visible = {(d["parent_event_id"], d["child_event_id"]) for d in deps}
            legend()
            st.graphviz_chart(dot_for(art["events"], art["dependencies"], names, source=chosen["raw"]["source_event_id"], target=chosen["raw"]["target_event_id"],
                                      path=chosen["result"]["dependency_path"], second_path=chosen["result"].get("second_path"), hidden_events=set(hidden),
                                      hidden_edges={(d["parent_event_id"], d["child_event_id"]) for d in art["dependencies"]} - visible), use_container_width=True)
        with st.expander("AI-visible logs"):
            st.code(task["ai_visible_logs"], language="text")
        with st.expander("Exact prompt"):
            st.code(attempt.get("prompt") or build_prompt(task), language="text")

with runner:
    try:
        stack_up = httpx.get(f"{GATEWAY_URL}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        stack_up = False
    st.subheader("Execute a scenario on the live services")
    if stack_up:
        st.success("Live stack reachable.")
        run_sid = st.selectbox("Scenario to execute", list(SCENARIO_TITLES), format_func=SCENARIO_TITLES.get)
        if st.button("Run scenario now", type="primary"):
            from app.scenarios.runner import run_and_save
            with st.spinner("Sending real requests and waiting for the stream to drain..."):
                live = run_and_save(BENCH / "live", [run_sid])[run_sid]
            st.session_state["live"] = live
        if "live" in st.session_state:
            live = st.session_state["live"]
            names_live = reverse_names(live)
            st.markdown(f"Run `{live['run_id']}`: **{live['integrity']['event_count']} events**, **{live['integrity']['edge_count']} edges**, integrity valid: **{live['integrity']['valid']}**. Saved under `benchmark/live/`, separate from the canonical benchmark.")
            st.graphviz_chart(dot_for(live["events"], live["dependencies"], names_live), use_container_width=True)
    else:
        st.warning("Live stack not reachable from here. Replaying captured benchmark evidence below (start it with `docker compose up --build`).")
    st.subheader("Replay captured evidence")
    replay = st.selectbox("Captured scenario", list(SCENARIO_TITLES), format_func=SCENARIO_TITLES.get, key="replay")
    art = artifact(replay)
    names = reverse_names(art)
    table = pd.DataFrame([{"time (UTC)": e["timestamp"][11:23], "request": e["metadata"].get("label"), "service": e["service"], "event": names[e["event_id"]], "status": e["status"], "message": e["metadata"].get("message")} for e in art["events"]])
    st.dataframe(table, hide_index=True, use_container_width=True, height=320)

    st.subheader("Ask a model, then verify")
    config = ModelConfig.from_env()
    ask_task = st.selectbox("Benchmark task", sorted(tasks), key="ask")
    if config is None:
        st.info("No model configured (OPENAI_API_KEY and MODEL_NAME are unset), so no AI call can be made. Handcrafted fixtures remain available in the other tabs.")
    else:
        st.caption(f"Model: {config.model}. A fresh conversation with one user message. This demo call is not part of the benchmark's first attempts.")
        if st.button("Ask model"):
            with st.spinner("Waiting for the model..."):
                completion = complete_once(config, build_prompt(tasks[ask_task]))
            parsed, error = parse_answer(completion.raw_text)
            ask_truth = load_truth(ask_task)
            ask_art = artifact(tasks[ask_task]["scenario_id"])
            st.code(completion.raw_text or "", language="json")
            if parsed is None:
                st.error(f"Format failure: {error}")
            else:
                ask_graph, ask_cov = task_view(ask_art, ask_truth)
                for c in parsed.claims:
                    render_verdict(verify_claim(c, ask_graph, ask_cov), reverse_names(ask_art))

with experiments:
    if real_sets:
        for name in real_sets:
            m = json.loads((results[name] / "metrics.json").read_text())
            st.subheader(f"{m['model_name']} · {m['condition']}")
            st.caption(f"Evaluated {', '.join(m['evaluated_dates_utc'])} UTC. First attempts, one fresh conversation per task. A small benchmark: observed failure modes in these cases, not a general finding about the model.")
            st.dataframe(pd.DataFrame([
                {"Measure": "Tasks fully correct", "Count": f"{m['tasks_correct']}/{m['tasks_evaluated']}"},
                {"Measure": "Claims accepted", "Count": f"{m['claims_accepted']}/{m['claims_total']}"},
                {"Measure": "False-positive reachability", "Count": f"{m['false_positive_reachability']['count']}/{m['false_positive_reachability']['denominator']}"},
                {"Measure": "False-negative reachability (complete traces)", "Count": f"{m['false_negative_reachability_complete']['count']}/{m['false_negative_reachability_complete']['denominator']}"},
                {"Measure": "Incorrect certainty (incomplete traces)", "Count": f"{m['incorrect_certainty_incomplete']['count']}/{m['incorrect_certainty_incomplete']['denominator']}"},
                {"Measure": "Format failures", "Count": f"{m['format_failures']}/{m['tasks_evaluated']}"},
            ]), hide_index=True, use_container_width=True)
            label = {"temporal_correlation": "Temporal correlation", "retry_attribution": "Retry attribution", "partial_observability": "Partial observability", "shared_ancestor": "Shared ancestor"}
            st.dataframe(pd.DataFrame([{"Failure mode": label.get(k, k), "Tasks correct": f"{v['correct']}/{v['tasks']}", "Incorrect": v["incorrect"], "Format failures": v["format_failures"]}
                                       for k, v in sorted(m["error_categories"].items())]), hide_index=True, use_container_width=True)
    else:
        st.info("No model attempts exist, so no AI metrics are shown. Nothing here is estimated or simulated. Run `make evaluate` with credentials configured.")
    st.subheader("Verifier correctness (tests actually executed)")
    tests_path, mutation_path = BENCH / "results" / "verifier_tests.json", BENCH / "results" / "mutation_check.json"
    if tests_path.exists():
        t = json.loads(tests_path.read_text())
        cols = st.columns(3)
        cols[0].metric("Tests passed", t["passed"])
        cols[1].metric("Tests failed", t["failed"])
        cols[2].metric("Tests skipped", t["skipped"])
        st.dataframe(pd.DataFrame([{"Test file": k, **v} for k, v in t["per_file"].items()]), hide_index=True, use_container_width=True)
    if mutation_path.exists():
        mu = json.loads(mutation_path.read_text())
        st.markdown(f"**Planted-bug check:** {mu['caught']}/{mu['total']} deliberately broken verifier variants were caught by the offline tests.")
        st.dataframe(pd.DataFrame([{"Planted bug": r["mutation"], "Caught": "yes" if r["caught"] else "NO", "Test result": r["pytest"]} for r in mu["results"]]), hide_index=True, use_container_width=True)

with real_traces:
    rc = BENCH / "rcaeval"
    st.subheader("External validation on real microservice traces")
    st.caption("RCAEval (Pham et al., WWW 2025; MIT licence): a fixed sample of 16 cases at a pinned revision, chosen by rule before any model ran. "
               "Not produced by DAGScope's services, and reported separately from the 12 primary tasks. Coverage is always open-world here.")
    validation_path = rc / "results" / "verifier_on_real_traces.json"
    if validation_path.exists():
        v = json.loads(validation_path.read_text())["totals"]
        cols = st.columns(4)
        cols[0].metric("Real spans checked", f"{v['spans']:,}")
        cols[1].metric("Dangling parent links", f"{v['dangling_edges']:,}")
        cols[2].metric("Oracle disagreements", v["oracle_disagreements"])
        cols[3].metric("False verdicts under masking", v["false_support"] + v["wrongly_contradicted"] + v["negative_accepted"])
        st.markdown(f"**Masking check:** with 10% of spans hidden, over {v['masked_pairs']:,} claim pairs, {v['true_paths_hidden_by_mask']:,} true paths became NOT_OBSERVED and none were falsely decided. "
                    f"**Real gaps:** {v['cases_with_gaps']}/{v['cases']} cases have dangling parent links, without any masking by us.")
    runs = sorted((rc / "results").glob("*/metrics.json"))
    if not runs:
        st.info("No model attempts exist for this track yet.")
    else:
        run_dir = runs[0].parent
        m = json.loads(runs[0].read_text())
        rows = json.loads((run_dir / "per_task.json").read_text())
        attr, reach = m["attribution"], m["reachability"]
        st.success(f"Real first attempts from **{m['model_name']}**, evaluated {', '.join(m['evaluated_dates_utc'])} UTC.")
        cols = st.columns(4)
        cols[0].metric("Injected service identified", f"{attr['correct']}/{attr['tasks']}")
        cols[1].metric("Error-blame baseline", f"{attr['naive_correct']}/{attr['tasks']}")
        cols[2].metric("Reachability + gap tasks correct", f"{reach['tasks_correct']}/{reach['tasks_evaluated']}")
        cols[3].metric("Incorrect certainty (partial traces)", f"{reach['incorrect_certainty_incomplete']['count']}/{reach['incorrect_certainty_incomplete']['denominator']}")
        st.markdown("**Fault attribution**, checked against the experiment's injection label. This is intervention evidence, not graph reachability.")
        attribution = [r for r in rows if r["family"] == "attribution"]
        st.dataframe(pd.DataFrame([{"": VERDICT_ICON[r["verdict"]] if r["verdict"] else "⚠️", "Case": r["case"], "Injected into": r["injected_service"], "Model claimed": r["claimed"], "Confidence": r["confidence"],
                                    "Error-blame baseline": r["naive_baseline"], "Reports spans": r["service_present_in_traces"]} for r in attribution]), hide_index=True, use_container_width=True)
        st.markdown("**Inspect an attempt**")
        chosen_id = st.selectbox("Task", sorted(r["task_id"] for r in rows), index=sorted(r["task_id"] for r in rows).index("rcaeval_attr_re3ob_currencyservice_f1_1") if any(r["task_id"] == "rcaeval_attr_re3ob_currencyservice_f1_1" for r in rows) else 0)
        row = next(r for r in rows if r["task_id"] == chosen_id)
        record = json.loads((rc / "attempts" / run_dir.name / f"{chosen_id}.json").read_text())
        public = json.loads((rc / "tasks" / f"{chosen_id}.json").read_text())
        st.markdown(f"**Question.** {public['question']}")
        if record["parsed"]:
            st.markdown(f"*{record['parsed']['explanation']}*")
            st.caption(f"Stated confidence: {record['parsed']['confidence']}")
        if row["family"] == "attribution":
            if row["result"]:
                st.markdown(f"{VERDICT_ICON[row['verdict']]} **{row['verdict']}** · {row['result']['explanation']}")
            st.code(public["telemetry_table"], language="text")
        else:
            extract = json.loads((rc / "extracts" / f"{chosen_id}.json").read_text())
            extract_names = reverse_names(extract)
            for c in row["claims"]:
                render_verdict(c["result"], extract_names)
            if row["claims"]:
                highlight = row["claims"][0]
                st.graphviz_chart(dot_for(extract["events"], extract["dependencies"], extract_names, source=highlight["raw"]["source_event_id"], target=highlight["raw"]["target_event_id"],
                                          path=highlight["result"]["dependency_path"]), use_container_width=True)
            if row["mismatch"]:
                st.warning(row["mismatch"])
        with st.expander("Exact prompt"):
            st.code(record["prompt"], language="text")
        with st.expander("Raw response, verbatim"):
            st.code(record["raw_response"] or "", language="json")
