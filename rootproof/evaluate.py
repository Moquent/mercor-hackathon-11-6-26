"""One command: verify every saved AI attempt, self-test the verifier, audit extraction, write ground truth + report.

    python3 rootproof/evaluate.py          -> results/summary.json, results/report.html, ground_truth.md
"""
import html
import json
import os
from concurrent.futures import ThreadPoolExecutor

from incidents import ACTIONS, ENTRY, INCIDENTS, topo_for
from llm import ROOT
from render import clock
from selftest import AUDIT, REFERENCE, TRAP
from sim import JUDGE, PAGE_T, SLO_OK, run
from verify import ground_truth, ops_for, replay, verify

RESULTS = os.path.join(ROOT, "results")
RUNS = (1, 2, 3)


def load_attempt(task, r):
    p = os.path.join(ROOT, "attempts", f"{task}.run{r}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def evaluate():
    by_id = {i["id"]: i for i in INCIDENTS}
    jobs = [("ai", i["id"], r) for i in INCIDENTS for r in RUNS if load_attempt(i["id"], r)]
    jobs += [("reference", i["id"], 0) for i in INCIDENTS] + [("trap", i["id"], 0) for i in INCIDENTS]

    def go(job):
        kind, tid, r = job
        text = load_attempt(tid, r)["answer"] if kind == "ai" else (REFERENCE if kind == "reference" else TRAP)[tid]
        return job, verify(by_id[tid], text)

    with ThreadPoolExecutor(8) as ex:
        out = dict(ex.map(go, jobs))
    with ThreadPoolExecutor(8) as ex:
        truth = dict(zip([i["id"] for i in INCIDENTS], ex.map(ground_truth, INCIDENTS)))
    return by_id, out, truth


def audit(out):
    agree = total = 0
    rows = []
    for tid, labels in AUDIT.items():
        got = set(out[("ai", tid, 1)]["extraction"]["actions"])
        for a in ACTIONS:
            total += 1
            agree += (a in got) == (a in labels)
        rows.append({"task": tid, "hand_labels": sorted(labels), "jev": sorted(got), "exact_match": got == labels})
    return {"decisions_agreeing": agree, "decisions_total": total, "rows": rows}


def curve(inc, action_ids):
    rows = run(topo_for(inc), inc["faults"], ENTRY, ops_for(action_ids))
    return [(r["t"], r["err"]) for r in rows if r["svc"] == ENTRY and r["t"] % 5 == 0 and r["t"] >= 60]


def svg_chart(series, w=560, h=170):
    """series: list of (label, css var, [(t, err)])"""
    x0, x1, pad = 60, 600, 34
    X = lambda t: pad + (t - x0) / (x1 - x0) * (w - pad - 8)
    Y = lambda e: 8 + (1 - e) * (h - 30)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="entry error rate over time">']
    for e in (0, 0.5, 1):
        parts.append(f'<line x1="{pad}" x2="{w - 8}" y1="{Y(e):.1f}" y2="{Y(e):.1f}" class="grid"/>'
                     f'<text x="{pad - 6}" y="{Y(e) + 4:.1f}" class="ax" text-anchor="end">{int(e * 100)}%</text>')
    parts.append(f'<rect x="{X(JUDGE[0]):.1f}" y="6" width="{X(JUDGE[1]) - X(JUDGE[0]):.1f}" height="{h - 28}" class="judge"/>')
    parts.append(f'<line x1="{X(PAGE_T):.1f}" x2="{X(PAGE_T):.1f}" y1="6" y2="{h - 22}" class="page"/>'
                 f'<text x="{X(PAGE_T) - 4:.1f}" y="16" class="ax" text-anchor="end">actions applied {clock(PAGE_T)}</text>')
    for t in (60, 180, 300, 420, 540):
        parts.append(f'<text x="{X(t):.1f}" y="{h - 6}" class="ax" text-anchor="middle">{clock(t)[:5]}</text>')
    for label, var, pts in series:
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(t):.1f},{Y(e):.1f}" for i, (t, e) in enumerate(pts))
        parts.append(f'<path d="{d}" style="stroke:var({var})" class="line"><title>{html.escape(label)}</title></path>')
    parts.append("</svg>")
    return "".join(parts)


def write_ground_truth(by_id, out, truth):
    lines = ["# Ground truth", "",
             "Each task is a seeded simulation, so the right answer is **computed**: every executable action in the catalog (singles, then",
             f"pairs) is replayed from the page time ({clock(PAGE_T)}), and a set counts as a fix if the `{ENTRY}` error rate stays under",
             f"{SLO_OK:.0%} over {clock(JUDGE[0])}–{clock(JUDGE[1])}. The rationale explains *why* in plain terms; the replay numbers are the proof.", ""]
    for tid, inc in by_id.items():
        noop = replay(inc, [])[1]
        trap = out[("trap", tid, 0)]
        lines += [f"## {tid} — {inc['title']}", "", f"**What happened:** {inc['truth']}", ""]
        if inc.get("no_outage"):
            lines += [f"**Correct response:** no production change (entry error rate {noop:.1%} throughout).", ""]
        else:
            lines += [f"**Minimal sufficient fixes (computed):** " + "; ".join(" + ".join(f"`{a}`" for a in s) for s in truth[tid]), "",
                      f"**Without action:** entry error rate {noop:.0%} in the judged window.", ""]
        lines += [f"**Tempting wrong answer:** {json.loads(TRAP[tid].split('```json')[1].split('```')[0])['actions']} → verifier: "
                  f"**{trap['verdict']}** (entry error rate after replay {trap['ai_error_rate_after']:.0%}).", ""]
    open(os.path.join(ROOT, "ground_truth.md"), "w").write("\n".join(lines))


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--card:#ffffff;--line:#e4e1d8;--ok:#1f7a4d;--bad:#b3261e;--warn:#9a6700;
--s-noop:#9a9890;--s-ai:#c2410c;--s-fix:#1f6feb;--chip:#f1efe8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141412;--fg:#ecebe6;--muted:#a3a19a;--card:#1d1d1a;--line:#33322d;
--ok:#4ac08a;--bad:#ff7b72;--warn:#e3b341;--s-noop:#7d7b74;--s-ai:#ff8a4c;--s-fix:#58a6ff;--chip:#2a2925}}
:root[data-theme="dark"]{--bg:#141412;--fg:#ecebe6;--muted:#a3a19a;--card:#1d1d1a;--line:#33322d;--ok:#4ac08a;--bad:#ff7b72;--warn:#e3b341;
--s-noop:#7d7b74;--s-ai:#ff8a4c;--s-fix:#58a6ff;--chip:#2a2925}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 60px}h1{font-size:28px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.tile b{display:block;font-size:24px}
.tile span{color:var(--muted);font-size:13px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.wrap{overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
.card h3{margin:0 0 6px;font-size:16px}.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:700;
border:1px solid currentColor}.ACCEPT{color:var(--ok)}.REJECT{color:var(--bad)}.PARTIAL{color:var(--warn)}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;margin:2px 4px 2px 0;font:12px ui-monospace,Menlo,monospace}
.chart{width:100%;height:auto;max-width:620px}.grid{stroke:var(--line)}.ax{fill:var(--muted);font-size:10px}
.page{stroke:var(--muted);stroke-dasharray:3 3}.judge{fill:var(--chip);opacity:.6}.line{fill:none;stroke-width:2}
.legend span{margin-right:14px;font-size:13px;color:var(--muted)}.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px}
ul{margin:6px 0;padding-left:20px}li.fail{color:var(--bad)}li.pass{color:var(--muted)}details{margin-top:8px}
pre{white-space:pre-wrap;background:var(--chip);padding:10px;border-radius:8px;font-size:12.5px;max-height:320px;overflow:auto}
.cert{border-left:3px solid var(--bad);padding-left:10px;margin:8px 0}
"""


def write_report(by_id, out, truth, aud):
    ai1 = [out[("ai", t, 1)] for t in by_id]
    all_ai = [v for (k, _, _), v in out.items() if k == "ai"]
    acc1 = sum(v["verdict"] == "ACCEPT" for v in ai1)
    acc_all = sum(v["verdict"] == "ACCEPT" for v in all_ai)
    ref_ok = sum(out[("reference", t, 0)]["verdict"] == "ACCEPT" for t in by_id)
    trap_ok = sum(out[("trap", t, 0)]["verdict"] != "ACCEPT" for t in by_id)
    model = load_attempt(next(iter(by_id)), 1)["model_reported"]
    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>RootProof Results</title><style>{CSS}</style></head><body><main>",
         "<h1>RootProof</h1><p class=sub>Verifying an AI's incident diagnosis by replaying the outage with its fix applied. "
         f"Subject AI: <code>{model}</code>, one fresh request per task. Extraction: TypeSafe Jev. Decision: deterministic replay.</p>",
         "<div class=tiles>",
         f"<div class=tile><b>{len(by_id)}</b><span>incidents (fictional Larkwave platform)</span></div>",
         f"<div class=tile><b>{acc1}/{len(ai1)}</b><span>first attempts accepted; {len(ai1) - acc1} caught</span></div>",
         f"<div class=tile><b>{acc_all}/{len(all_ai)}</b><span>accepted across 3 runs per task</span></div>",
         f"<div class=tile><b>{ref_ok + trap_ok}/{2 * len(by_id)}</b><span>verifier self-test (correct accepted + traps caught)</span></div>",
         f"<div class=tile><b>{aud['decisions_agreeing']}/{aud['decisions_total']}</b><span>Jev extraction decisions matching hand labels</span></div>",
         "</div>"]
    h.append("<h2>Main finding: the model reverts the trigger and misses the loop that keeps the outage going</h2><div class=wrap><table>"
             "<tr><th>task</th><th>what the trigger looks like</th><th>run 1</th><th>run 2</th><th>run 3</th></tr>")
    for t in by_id:
        vs = [out.get(("ai", t, r), {}).get("verdict", "-") for r in RUNS]
        h.append(f"<tr><td>{t}</td><td>{html.escape(by_id[t]['title'])}</td>" + "".join(f"<td><span class='badge {v}'>{v}</span></td>" for v in vs) + "</tr>")
    h.append("</table></div><p class=sub>The model handles textbook traps: post-hoc deploys, loud-but-irrelevant errors, fallback floods, phantom alerts. "
             "When a single config change is visibly the trigger (02, 12, 13), it tends to propose only the revert. In replay, the layered retries "
             "(x8 load) keep media-cache saturated after the revert, so errors stay at 100%.</p>")
    h.append("<h2>Every task: first attempt</h2><p class=legend><span><i style='background:var(--s-noop)'></i>no action</span>"
             "<span><i style='background:var(--s-ai)'></i>AI's actions replayed</span><span><i style='background:var(--s-fix)'></i>computed minimal fix</span>"
             "<span>shaded = judged window; orange hides grey when the AI's actions change nothing</span></p>")
    for t, inc in by_id.items():
        v = out[("ai", t, 1)]
        att = load_attempt(t, 1)
        blk = v["extraction"]["json_block"] or {}
        fix = list(truth[t][0]) if truth[t] else []
        chart = svg_chart([("no action", "--s-noop", curve(inc, [])), ("computed fix", "--s-fix", curve(inc, fix)),
                           ("AI's actions", "--s-ai", curve(inc, list(v["extraction"]["actions"])))])
        acts = "".join(f"<li>{html.escape(str(a))}</li>" for a in blk.get("actions", [])) or "<li>(no JSON block)</li>"
        ex = "".join(f"<span class=chip>{a} p={p:.2f}</span>" for a, p in v["extraction"]["actions"].items()) or "<span class=chip>no production action</span>"
        inv = "".join(f"<li class={'pass' if i['pass'] else 'fail'}>{'✓' if i['pass'] else '✗'} <b>{i['check']}</b>: {html.escape(i['detail'])}</li>" for i in v["invariants"])
        certs = "".join(f"<div class=cert>{html.escape(c)}</div>" for c in v["certificates"])
        h.append(f"<div class=card><h3>{t} · {html.escape(inc['title'])} <span class='badge {v['verdict']}'>{v['verdict']}</span></h3>"
                 f"<div><b>AI blamed:</b> <code>{html.escape(str(blk.get('root_cause_service')))}</code> · <b>AI's actions:</b><ul>{acts}</ul></div>"
                 f"<div><b>Compiled to:</b> {ex}</div>{chart}{certs}<details><summary>claim checks</summary><ul>{inv}</ul></details>"
                 f"<details><summary>ground truth</summary><p>{html.escape(inc['truth'])}</p><p>Minimal fixes: "
                 + "; ".join(" + ".join(f"<span class=chip>{a}</span>" for a in s) for s in truth[t]) + "</p></details>"
                 f"<details><summary>full first answer ({att['model_reported']}, {att['asked_at_utc'][:19]}Z)</summary><pre>{html.escape(att['answer'])}</pre></details></div>")
    h.append("<h2>Verifier self-test</h2><div class=wrap><table><tr><th>task</th><th>reference answer</th><th>trap answer</th><th>why the trap fails</th></tr>")
    for t in by_id:
        r, tr = out[("reference", t, 0)], out[("trap", t, 0)]
        h.append(f"<tr><td>{t}</td><td><span class='badge {r['verdict']}'>{r['verdict']}</span></td><td><span class='badge {tr['verdict']}'>{tr['verdict']}</span></td>"
                 f"<td>{html.escape(tr['certificates'][0] if tr['certificates'] else '')}</td></tr>")
    h.append("</table></div><h2>Extraction audit (run 1)</h2><div class=wrap><table><tr><th>task</th><th>hand labels</th><th>Jev</th><th>match</th></tr>")
    for row in aud["rows"]:
        h.append(f"<tr><td>{row['task']}</td><td>{', '.join(row['hand_labels']) or '—'}</td><td>{', '.join(row['jev']) or '—'}</td><td>{'✓' if row['exact_match'] else '✗'}</td></tr>")
    h.append("</table></div><h2>How it works and its limits</h2><ul>"
             "<li><b>Decision = replay.</b> Each answer's actions are compiled into interventions and replayed in the same seeded simulation from the page time. "
             f"ACCEPT requires the entry error rate to stay under {SLO_OK:.0%} in the judged window <i>and</i> the blamed service to pass the claim checks.</li>"
             "<li><b>Fair to alternatives.</b> Any action set that works in replay is accepted. The answer key is computed by brute force, not written.</li>"
             "<li><b>Explanations.</b> Request-path reachability, baseline (pre-existing noise), temporal order, origin vs victim, retry amplification, "
             "and the minimum scale factor that would have worked.</li>"
             "<li><b>The one learned component is Jev</b>, which maps prose to a fixed action catalog with probabilities. It is audited above. "
             "Actions outside the catalog are ignored, so it can under-credit an unusual but valid fix.</li>"
             "<li><b>Limits:</b> a fluid-queue simulation, not a real cluster; one subject model; 13 tasks.</li></ul></main></body></html>")
    open(os.path.join(RESULTS, "report.html"), "w").write("".join(h))


def node_states(inc):
    rows = run(topo_for(inc), inc["faults"], ENTRY)
    at = {(r["t"], r["svc"]): r for r in rows}
    out = {}
    for n in topo_for(inc):
        b, p = at[(60, n)], at[(PAGE_T, n)]
        idle = p["rps"] < 1
        out[n] = {"err": None if idle else round(p["err"], 3), "idle": idle,
                  "load_x": round(p["rps"] / max(1.0, b["rps"]), 2) if b["rps"] > 5 else None,
                  "lat_x": round(p["lat_ms"] / max(1.0, b["lat_ms"]), 2)}
    return out


def site_data(by_id, out, truth, aud):
    from chain import score as chain_score
    from rcaeval_verify import evaluate as rc_eval
    tasks = []
    with ThreadPoolExecutor(8) as ex:
        chains = dict(zip(by_id, ex.map(lambda t: chain_score(by_id[t], load_attempt(t, 1)["answer"]), by_id)))
    for t, inc in by_id.items():
        v = out[("ai", t, 1)]
        att = load_attempt(t, 1)
        fix = list(truth[t][0]) if truth[t] else []
        tasks.append({
            "id": t, "title": inc["title"], "truth": inc["truth"], "no_outage": bool(inc.get("no_outage")),
            "verdicts": [out.get(("ai", t, r), {}).get("verdict") for r in RUNS],
            "first": {"verdict": v["verdict"], "answer": att["answer"], "model": att["model_reported"], "asked": att["asked_at_utc"],
                      "json": v["extraction"]["json_block"], "roots": v["extraction"]["roots"], "actions": v["extraction"]["actions"],
                      "invariants": v["invariants"], "certificates": v["certificates"], "err_after": v["ai_error_rate_after"]},
            "fixes": [list(x) for x in truth[t]],
            "curves": {"noop": curve(inc, []), "fix": curve(inc, fix), "ai": curve(inc, list(v["extraction"]["actions"]))},
            "chain": chains[t], "nodes": node_states(inc),
            "events": [[clock(e[0]) if e[0] >= 0 else "yesterday", e[1], e[2], e[3]] for e in sorted(inc["events"])],
            "reference": out[("reference", t, 0)]["verdict"], "trap": out[("trap", t, 0)]["verdict"],
            "trap_cert": (out[("trap", t, 0)]["certificates"] or [""])[0],
            "trap_actions": json.loads(TRAP[t].split("```json")[1].split("```")[0])["actions"],
            "prompt": open(os.path.join(ROOT, "tasks", t, "prompt.txt")).read(),
        })
    rc_rows, rc_sum = rc_eval("test")
    _, rc_dev = rc_eval("dev")
    topo = topo_for(INCIDENTS[0])
    all_ai = [v for (k, _, _), v in out.items() if k == "ai"]
    ch = list(chains.values())
    return {
        "model": load_attempt(INCIDENTS[0]["id"], 1)["model_reported"],
        "topology": {"nodes": list(topo), "edges": [[c, p] for p, s in topo.items() for c in s.deps],
                     "jobs": [["sub-poller", "docstore"], ["msg-cleanup", "notify-relay"]],
                     "retries": {n: s.retries for n, s in topo.items()}},
        "tasks": tasks,
        "stats": {"tasks": len(by_id), "first_accept": sum(t["first"]["verdict"] == "ACCEPT" for t in tasks),
                  "runs": len(all_ai), "runs_accept": sum(v["verdict"] == "ACCEPT" for v in all_ai),
                  "selftest_ok": sum(out[("reference", t, 0)]["verdict"] == "ACCEPT" for t in by_id)
                  + sum(out[("trap", t, 0)]["verdict"] != "ACCEPT" for t in by_id), "selftest_n": 2 * len(by_id),
                  "audit_ok": aud["decisions_agreeing"], "audit_n": aud["decisions_total"],
                  "chain_precision": sum(c["precision"] for c in ch) / len(ch), "chain_recall": sum(c["recall"] for c in ch) / len(ch)},
        "audit": aud["rows"],
        "rcaeval": {"test": rc_sum, "dev": rc_dev, "rows": rc_rows,
                    "deps": {"frontend": ["adservice", "cartservice", "checkoutservice", "currencyservice", "productcatalogservice",
                                          "recommendationservice", "shippingservice"],
                             "checkoutservice": ["cartservice", "currencyservice", "emailservice", "paymentservice",
                                                 "productcatalogservice", "shippingservice"],
                             "recommendationservice": ["productcatalogservice"], "cartservice": ["redis"]}},
    }


if __name__ == "__main__":
    from site_build import build
    os.makedirs(RESULTS, exist_ok=True)
    by_id, out, truth = evaluate()
    aud = audit(out)
    write_ground_truth(by_id, out, truth)
    data = site_data(by_id, out, truth, aud)
    build(data, os.path.join(RESULTS, "report.html"))
    summary = {"verdicts": [{"kind": k, "task": t, "run": r, **v} for (k, t, r), v in out.items()],
               "ground_truth": {t: [list(s) for s in v] for t, v in truth.items()}, "extraction_audit": aud,
               "causal_chains": {t["id"]: t["chain"] for t in data["tasks"]}, "rcaeval": data["rcaeval"], "stats": data["stats"]}
    json.dump(summary, open(os.path.join(RESULTS, "summary.json"), "w"), indent=2, default=list)
    for t in by_id:
        print(f"{t:22s} " + " ".join(out.get(("ai", t, r), {}).get("verdict", "-") for r in RUNS)
              + f" | ref {out[('reference', t, 0)]['verdict']} trap {out[('trap', t, 0)]['verdict']}")
    print(json.dumps(data["stats"], indent=1))
    print("rcaeval test:", json.dumps(data["rcaeval"]["test"]))
