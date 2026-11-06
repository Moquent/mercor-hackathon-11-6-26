"""Reference-free claim checks on REAL telemetry (RCAEval RE2-OB). Replay is impossible on recorded data, so the verifier
checks the AI's causal claim against the metrics and the call graph. The fault label is used only afterwards, to score the verifier.

Checks
  evidence   the blamed service actually shows an anomaly after the incident starts
  signature  the claimed fault type shows up in that service's own matching metric (cpu->cpu, mem->mem, disk->diskio,
             socket->socket, delay->latency/caller latency, loss->errors/caller errors)
  temporal   the blamed service degrades no later than the other services on the claimed path
  path       every hop of the claimed propagation path is a real call edge (callee -> caller) and it ends at the frontend
"""
import json
import os
import re

from llm import ROOT

DEPS = {"frontend": ["adservice", "cartservice", "checkoutservice", "currencyservice", "productcatalogservice",
                     "recommendationservice", "shippingservice"],
        "checkoutservice": ["cartservice", "currencyservice", "emailservice", "paymentservice", "productcatalogservice", "shippingservice"],
        "recommendationservice": ["productcatalogservice"], "cartservice": ["redis"]}
SERVICES = ["frontend", "adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice", "paymentservice",
            "productcatalogservice", "recommendationservice", "redis", "shippingservice"]
CALLERS = {s: [p for p, cs in DEPS.items() if s in cs] for s in SERVICES}
SIGNATURE = {"cpu": ["cpu"], "mem": ["mem"], "disk": ["diskio"], "socket": ["socket"], "delay": ["latency-90"], "loss": ["error", "latency-90"]}
Z = 4.0
RESOURCE_Z = 20.0
TEMPORAL_SLACK = 1


def norm(name):
    n = re.sub(r"[^a-z]", "", str(name).lower())
    for s in SERVICES:
        if n == s or n == s.replace("service", "") or (len(n) > 3 and s.startswith(n)):
            return s
    return None


def zscores(series, k0):
    base = [v for v in series[:k0] if v is not None]
    if len(base) < 3:   # series absent before the incident: appearing at all is the anomaly
        return [0.0] * k0 + [0.0 if not v else 100.0 for v in series[k0:]]
    mu = sum(base) / len(base)
    sd = (sum((v - mu) ** 2 for v in base) / len(base)) ** 0.5
    scale = max(sd, 0.05 * abs(mu), 1e-6)
    return [0.0 if v is None else abs(v - mu) / scale for v in series]


def profile(f):
    """Per service: max z per metric after injection minus 2 min, and onset minute of the first z > Z."""
    k0 = f["inject_minute"] - 2
    prof = {}
    for s, ms in f["metrics"].items():
        if not ms:
            continue
        per, onset = {}, None
        for mt, series in ms.items():
            vals = [v for v in series if v is not None]
            if not vals or max(vals) == min(vals):
                continue                                   # a flat series carries no information (often: not really collected)
            z = zscores(series, k0)
            per[mt] = max(z[k0:]) if len(z) > k0 else 0.0
            hit = next((k for k in range(k0, len(z)) if z[k] > Z), None)
            if hit is not None and mt not in ("workload",):
                onset = hit if onset is None else min(onset, hit)
        logs = f["error_logs"].get(s)
        if logs:
            lz = zscores(logs, k0)
            per["logs"] = max(lz[k0:])
            hit = next((k for k in range(k0, len(lz)) if lz[k] > Z), None)
            if hit is not None:
                onset = hit if onset is None else min(onset, hit)
        prof[s] = {"z": per, "score": max([v for k, v in per.items() if k != "workload"] or [0]), "onset": onset}
    return prof


def baseline_rank(prof):
    """Statistical baseline (BARO-like): rank services by their largest post-injection z-score."""
    return sorted(prof, key=lambda s: -prof[s]["score"])


def check(f, claim):
    prof = profile(f)
    svc = norm(claim.get("root_cause_service"))
    fault = str(claim.get("fault_type", "")).lower().strip()
    path = [norm(p) for p in claim.get("propagation_path", []) or []]
    checks = []
    if svc is None or svc not in prof:
        return [("evidence", False, f"Blamed component '{claim.get('root_cause_service')}' has no telemetry in this system.")], svc, path
    p = prof[svc]
    checks.append(("evidence", p["score"] > Z, f"{svc} peak anomaly z={p['score']:.1f} (threshold {Z})."))
    sig = [m for m in SIGNATURE.get(fault, []) if m in p["z"]]
    if fault not in SIGNATURE:
        checks.append(("signature", False, f"claimed fault type '{fault}' is not one of cpu/mem/disk/delay/loss/socket."))
    elif fault in ("delay", "loss"):
        res = {m: p["z"].get(m, 0) for m in ("cpu", "mem")}
        hot = {m: z for m, z in res.items() if z > RESOURCE_Z}
        if hot:
            checks.append(("signature", False, f"claimed network {fault}, but {svc}'s own " + ", ".join(f"{m} z={z:.0f}" for m, z in hot.items())
                           + " shows resource exhaustion, which network faults do not cause."))
        elif fault == "delay" and (errs := [c for c in [svc] + CALLERS[svc] if prof.get(c, {}).get("z", {}).get("logs", 0) > Z]):
            checks.append(("signature", False, f"claimed delay, but error logs surged at {', '.join(errs)}; pure delay produces "
                           "no errors (packet loss does)."))
        else:
            zs = [prof[c]["z"].get(m, 0) for c in [svc] + CALLERS[svc] if c in prof for m in SIGNATURE[fault]]
            checks.append(("signature", bool(zs) and max(zs) > Z, f"claimed {fault}: latency/error z={max(zs or [0]):.0f} at {svc} or its callers, "
                           "with no resource exhaustion."))
    elif sig:
        zmax = max(p["z"][m] for m in sig)
        checks.append(("signature", zmax > Z, f"claimed {fault}: {svc} {'/'.join(sig)} z={zmax:.1f}."))
    else:
        checks.append(("signature", None, f"{svc} does not export a {fault} metric; check not applicable."))
    others = [s for s in path[1:] if s and s in prof and prof[s]["onset"] is not None]
    if p["onset"] is not None and others:
        later = [s for s in others if prof[s]["onset"] < p["onset"] - TEMPORAL_SLACK]
        checks.append(("temporal", not later, f"{svc} anomaly starts at minute {p['onset']}" +
                       (f", but {', '.join(later)} degraded earlier." if later else "; nothing on its path degraded earlier.")))
    bad_hops = [f"{a}->{b}" for a, b in zip(path, path[1:]) if a and b and b not in CALLERS.get(a, [])]
    unknown = [x for x in claim.get("propagation_path", []) if norm(x) is None]
    ok_path = bool(path) and path[0] == svc and path[-1] == "frontend" and not bad_hops and not unknown
    checks.append(("path", ok_path, "propagation path is a valid call chain to frontend." if ok_path else
                   f"invalid path {claim.get('propagation_path')}: " + ", ".join(
                       (["does not start at the blamed service"] if path and path[0] != svc else []) +
                       (["does not end at frontend"] if path and path[-1] != "frontend" else []) +
                       [f"no call edge {h}" for h in bad_hops] + [f"unknown component {u}" for u in unknown])))
    return checks, svc, path


def parse(answer):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", answer, re.S)
    try:
        return json.loads(m[-1]) if m else {}
    except json.JSONDecodeError:
        return {}


def evaluate(split="test"):
    """split: test = instance 1 (held out), dev = instance 2 (used to design the signature rules)."""
    base = os.path.join(ROOT, "tasks", "rcaeval")
    rows = []
    suffix = "_1" if split == "test" else "_2"
    for case in sorted(os.listdir(base)):
        if not case.endswith(suffix):
            continue
        ap = os.path.join(ROOT, "attempts", "rcaeval", f"{case}.json")
        if not os.path.exists(ap):
            continue
        f = json.load(open(os.path.join(base, case, "features.json")))
        att = json.load(open(ap))
        claim = parse(att["answer"])
        checks, svc, path = check(f, claim)
        passed = all(ok is not False for _, ok, _ in checks)
        prof = profile(f)
        rank = baseline_rank(prof)
        fault = str(claim.get("fault_type", "")).lower()
        svc_ok = svc == f["truth_service"]
        fault_ok = svc_ok and fault == f["truth_fault"]
        service_level_fail = any(ok is False for n, ok, _ in checks if n in ("evidence", "temporal"))
        gated = rank[0] if service_level_fail else svc
        rows.append({"case": case, "truth_service": f["truth_service"], "truth_fault": f["truth_fault"], "ai_service": svc,
                     "ai_fault": fault, "ai_path": claim.get("propagation_path"), "ai_service_correct": svc_ok,
                     "ai_fault_correct": fault_ok, "verifier": "PASS" if passed else "FLAG",
                     "checks": [{"check": n, "pass": ok, "detail": d} for n, ok, d in checks],
                     "baseline_top3": rank[:3], "gated_service": gated, "gated_correct": gated == f["truth_service"],
                     "model": att["model_reported"], "asked_at_utc": att["asked_at_utc"]})
    n = len(rows)
    wrong = [r for r in rows if not r["ai_fault_correct"]]
    flagged = [r for r in rows if r["verifier"] == "FLAG"]
    tp = sum(1 for r in flagged if not r["ai_fault_correct"])
    summary = {
        "cases": n,
        "ai_service_acc": sum(r["ai_service_correct"] for r in rows) / n,
        "ai_service_and_fault_acc": sum(r["ai_fault_correct"] for r in rows) / n,
        "baseline_ac1": sum(r["baseline_top3"][0] == r["truth_service"] for r in rows) / n,
        "baseline_ac3": sum(r["truth_service"] in r["baseline_top3"] for r in rows) / n,
        "verifier_flag_precision": tp / len(flagged) if flagged else None,
        "verifier_flag_recall": tp / len(wrong) if wrong else None,
        "verifier_accuracy": sum((r["verifier"] == "FLAG") == (not r["ai_fault_correct"]) for r in rows) / n,
        "pass_precision": (sum(r["ai_fault_correct"] for r in rows if r["verifier"] == "PASS") /
                           max(1, sum(r["verifier"] == "PASS" for r in rows))),
        "gated_service_acc": sum(r["gated_correct"] for r in rows) / n,
        "flagged": len(flagged), "ai_wrong": len(wrong),
    }
    return rows, summary


if __name__ == "__main__":
    import sys
    rows, s = evaluate(sys.argv[1] if len(sys.argv) > 1 else "test")
    for r in rows:
        bad = [c["check"] for c in r["checks"] if c["pass"] is False]
        print(f"{r['case']:40s} AI={r['ai_service']}/{r['ai_fault']:7s} ok={int(r['ai_fault_correct'])} {r['verifier']} {bad} base={r['baseline_top3'][0]}")
    print(json.dumps(s, indent=1))
