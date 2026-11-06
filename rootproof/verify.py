"""RootProof verifier: typed extraction (Jev) -> counterfactual replay (decides) -> invariants (explain).

Verdicts
  ACCEPT  the AI's actions end the incident in replay and its blamed service passes every claim invariant
  PARTIAL the actions work but the causal story is wrong, or the story holds but the actions don't end the incident
  REJECT  the actions don't end the incident and the blamed service fails a claim invariant
"""
import json
import re
from itertools import combinations

from incidents import ACTIONS, ENTRY, SERVICES, topo_for
from llm import jev
from render import clock
from sim import JUDGE, PAGE_T, SLO_OK, error_rate, run

LOAD_SOURCES = {"sub-poller": "docstore", "msg-cleanup": "notify-relay"}
ROLLBACK_TARGET = {"rollback_session_api": "session-api", "rollback_notify_relay": "notify-relay",
                   "rollback_meta_index": "meta-index", "rollback_media_cache": "media-cache", "rollback_sub_poller": "sub-poller"}
JEV_THRESHOLD = 0.5


# ---------- 1. typed extraction ----------
def parse_json_block(text):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    try:
        return json.loads(m[-1]) if m else None
    except json.JSONDecodeError:
        return None


def extract(answer):
    """Jev answers typed questions about the AI's prose; it never sees ground truth or the telemetry."""
    block = parse_json_block(answer)
    state = answer if block is None else (
        f"Root cause service: {block.get('root_cause_service')}\nMechanism: {block.get('mechanism')}\n"
        f"Recommended actions:\n" + "\n".join(f"- {a}" for a in block.get("actions", [])))
    questions = {"blamed": {"type": "choice", "instructions": "Which single component does this diagnosis name as the root cause?",
                            "criteria": {s: s for s in SERVICES} | {"none": "no single component / unclear"}}}
    for k, (desc, _) in ACTIONS.items():
        questions[k] = {"type": "noul", "instructions": f"Does the recommended remediation include this action: {desc}?"}
    res = jev(state, questions)
    ans = res["answers"]
    picked = {k: ans[k]["noul"] for k in ACTIONS if ans[k]["noul"] >= JEV_THRESHOLD}
    named = str((block or {}).get("root_cause_service", ""))
    roots = [s for s in SERVICES if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", named)]
    if not roots and ans["blamed"]["choice"] != "none":
        roots = [ans["blamed"]["choice"]]
    return {"json_block": block, "jev_model": res.get("model"), "blamed": ans["blamed"]["choice"], "roots": roots,
            "blamed_confidence": ans["blamed"].get("confidence"), "actions": picked,
            "action_probs": {k: round(ans[k]["noul"], 3) for k in ACTIONS}}


# ---------- 2. counterfactual replay ----------
def ops_for(action_ids):
    return [op for a in action_ids for op in ACTIONS[a][1]]


def replay(inc, action_ids):
    rows = run(topo_for(inc), inc["faults"], ENTRY, ops_for(action_ids))
    return rows, error_rate(rows, ENTRY, *JUDGE)


def ground_truth(inc):
    """Minimal sufficient action sets, by brute force over singles then pairs of executable actions."""
    keys = [k for k, (_, ops) in ACTIONS.items() if ops]
    singles = [(k,) for k in keys if replay(inc, [k])[1] < SLO_OK]
    if singles:
        return singles
    return [p for p in combinations(keys, 2) if replay(inc, list(p))[1] < SLO_OK]


def min_scale(inc, action_ids, svc):
    others = [a for a in action_ids if not a.startswith(f"scale_{svc.replace('-', '_')}")]
    for k in [x / 2 for x in range(2, 41)]:
        rows = run(topo_for(inc), inc["faults"], ENTRY, ops_for(others) + [("scale", svc, k)])
        if error_rate(rows, ENTRY, *JUDGE) < SLO_OK:
            return k
    return None


# ---------- 3. claim invariants (computed from telemetry the AI saw) ----------
def series(rows, svc, key):
    return {r["t"]: r[key] for r in rows if r["svc"] == svc}


def onset(rows, svc):
    err, lat, rps = (series(rows, svc, k) for k in ("err", "lat_ms", "rps"))
    b_err = sum(err[t] for t in range(90)) / 90
    b_lat = sum(lat[t] for t in range(90)) / 90
    b_rps = sum(rps[t] for t in range(90)) / 90
    for t in range(90, PAGE_T):
        if err[t] > b_err + 0.05 or lat[t] > 2 * b_lat or rps[t] > 1.5 * b_rps + 5:
            return t
    return None


def reachable(topo, src=ENTRY):
    seen, stack = set(), [src]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack += topo[n].deps
    return seen


def invariants(inc, rows, roots):
    """Claim checks for every named root cause; the temporal check uses the earliest-degrading root."""
    if isinstance(roots, str):
        roots = [roots]
    roots = [r for r in roots if r != "none" and LOAD_SOURCES.get(r, r) in topo_for(inc)]
    t_inc = onset(rows, ENTRY)
    if not roots:
        return [("claim", False, "No root-cause component could be identified in the answer.")], t_inc
    checks = []
    starts = [onset(rows, LOAD_SOURCES.get(r, r)) for r in roots]
    first = min((t for t in starts if t is not None), default=None)
    per_root = {r: _root_checks(inc, rows, r, t_inc, first) for r in roots}
    clean = [r for r, cs in per_root.items() if all(ok for n, ok, _ in cs if n != "temporal")]
    temporal_ok = all(ok for cs in per_root.values() for n, ok, _ in cs if n == "temporal")
    for r, cs in per_root.items():
        for name, ok, msg in cs:
            soft = not ok and name != "temporal" and clean and r not in clean
            checks.append((name if not soft else f"{name} (extra root, warning)", ok or soft, msg))
    if not clean:
        checks.append(("claim", False, "No named root cause survives the origin/reachability/baseline checks."))
    elif not temporal_ok:
        pass
    return checks, t_inc


def _root_checks(inc, rows, blamed, t_inc, first):
    topo = topo_for(inc)
    checks = []
    svc = LOAD_SOURCES.get(blamed, blamed)
    on_path = svc in reachable(topo)
    checks.append(("reachability", on_path,
                   f"{blamed} is on the listener request path." if on_path else
                   f"{blamed} is not on any path from {ENTRY}; it cannot cause {ENTRY} errors."))
    err = series(rows, svc, "err")
    pre, during = sum(err[t] for t in range(90)) / 90, sum(err[t] for t in range(t_inc or 0, PAGE_T)) / max(1, PAGE_T - (t_inc or 0))
    pre_existing = pre > 0.05 and abs(during - pre) < 0.05
    checks.append(("baseline", not pre_existing,
                   f"{blamed} error rate was already {pre:.0%} before the incident and is {during:.0%} now: pre-existing noise."
                   if pre_existing else f"{blamed} changed during the incident (err {pre:.0%} -> {during:.0%})."))
    t_svc = onset(rows, svc)
    if t_svc is None:
        checks.append(("temporal", False, f"{blamed} shows no anomaly at all before the page."))
    elif t_svc == first:
        ok = t_inc is None or t_svc <= t_inc + 15
        checks.append(("temporal", ok, f"{blamed} degraded at {clock(t_svc)}; {ENTRY} errors began at {clock(t_inc)}."
                       + ("" if ok else " Effect precedes the claimed cause; something else started the incident.")))
    earlier = [c for c in topo[svc].deps if (oc := onset(rows, c)) is not None and t_svc is not None and oc <= t_svc
               and series(rows, c, "err")[PAGE_T] > 0.2]
    parents = [p for p in topo if svc in topo[p].deps and topo[p].mode == "fallback"]
    shifted = []
    for p in parents:
        chain = topo[p].deps
        shifted = [c for c in chain[:chain.index(svc)] if series(rows, c, "err")[PAGE_T] > 0.5]
    victim = bool(earlier or shifted)
    why = (f"its failures follow {', '.join(earlier)} which failed first" if earlier else
           f"its load came from fallback traffic after {', '.join(shifted)} failed" if shifted else "")
    checks.append(("origin", not victim, f"{blamed} is a victim: {why}." if victim else f"{blamed} is an origin, not a downstream victim."))
    return checks


def amplification(inc, rows):
    """Largest ratio of a service's received load to entry load at page time (retry storms)."""
    rps = {r["svc"]: r["rps"] for r in rows if r["t"] == PAGE_T}
    base = {r["svc"]: r["rps"] for r in rows if r["t"] == 60}
    ratios = {s: rps[s] / base[s] for s in rps if base[s] > 20}
    top = max(ratios.values())
    topo = topo_for(inc)
    depth = {}
    for n in [ENTRY] + list(topo):
        for c in topo[n].deps:
            depth[c] = depth.get(n, 0) + 1
    svc = min((s for s, r in ratios.items() if r >= 0.9 * top), key=lambda s: depth.get(s, 0))
    return svc, ratios[svc]


# ---------- verdict ----------
def verify(inc, answer):
    ex = extract(answer)
    rows_noop = run(topo_for(inc), inc["faults"], ENTRY)
    rows_ai, err_ai = replay(inc, list(ex["actions"]))
    fixed = err_ai < SLO_OK
    checks, t_inc = invariants(inc, rows_noop, ex["roots"])
    story_ok = all(ok for _, ok, _ in checks)
    verdict = "ACCEPT" if fixed and story_ok else "PARTIAL" if fixed or story_ok else "REJECT"
    certs = []
    if inc.get("no_outage"):
        base_err = error_rate(rows_noop, ENTRY, 0, PAGE_T)
        prod_actions = [a for a in ex["actions"] if ACTIONS[a][1]]
        verdict = "ACCEPT" if not prod_actions else "REJECT"
        if prod_actions:
            certs.append(f"There is no outage to fix: {ENTRY} error rate is {base_err:.1%} across the whole window, and request rates "
                         f"and the independent playback_sessions insert rate are flat. The answer still changes production: "
                         f"{', '.join(prod_actions)}.")
        checks = [("impact", not prod_actions, "No user-facing impact exists in the telemetry.")]
        return {"task": inc["id"], "verdict": verdict, "fixed_in_replay": True, "ai_error_rate_after": round(err_ai, 3),
                "extraction": ex, "invariants": [{"check": n, "pass": ok, "detail": m} for n, ok, m in checks],
                "certificates": certs, "incident_onset": None}
    if not fixed:
        certs.append(f"Replay with the AI's actions applied at {clock(PAGE_T)}: {ENTRY} error rate stays at {err_ai:.0%} "
                     f"over {clock(JUDGE[0])}-{clock(JUDGE[1])} (SLO {SLO_OK:.0%}).")
        if not ex["actions"]:
            certs.append("No executable action was recognised in the answer.")
        svc, ratio = amplification(inc, rows_noop)
        if ratio > 2 and not any("retries" in a for a in ex["actions"]):
            certs.append(f"{svc} is receiving {ratio:.1f}x its normal request rate from layered retries; "
                         f"the proposed actions leave that amplification in place.")
        for a in ex["actions"]:
            if a.startswith("scale_") or a.startswith("restart_media"):
                svc_ = "media-cache" if "media_cache" in a else "docstore" if "docstore" in a else "brio-origin"
                k = min_scale(inc, list(ex["actions"]), svc_)
                certs.append(f"Scaling {svc_} would need >= {k}x capacity to end the incident." if k else
                             f"No amount of {svc_} scaling (up to 20x) ends the incident.")
        for a in ex["actions"]:
            target = ROLLBACK_TARGET.get(a)
            for t, svc, kind, msg in inc["events"]:
                if svc == target and kind in ("deploy", "config") and t_inc is not None and t > t_inc:
                    certs.append(f"Temporal: the {svc} change you roll back ('{msg[:48]}') landed at {clock(t)}, "
                                 f"after {ENTRY} errors began at {clock(t_inc)}; it cannot be the cause.")
    certs += [msg for name, ok, msg in checks if not ok]
    return {"task": inc["id"], "verdict": verdict, "fixed_in_replay": fixed, "ai_error_rate_after": round(err_ai, 3),
            "extraction": ex, "invariants": [{"check": n, "pass": ok, "detail": m} for n, ok, m in checks],
            "certificates": certs, "incident_onset": clock(t_inc) if t_inc is not None else None}
