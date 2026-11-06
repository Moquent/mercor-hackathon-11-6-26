"""Reference-free claim checks on real telemetry (RCAEval RE2-OB).

Replay is impossible on recorded data, so the verifier checks the AI's causal claim against the metrics and the call
graph. The fault label is only read afterwards, to score the verifier.

  evidence   the blamed service shows an anomaly after the incident starts
  signature  the claimed fault type appears in that service's own matching metric
             (cpu->cpu, mem->mem, disk->diskio, socket->socket; delay and loss are judged on latency, errors and callers)
  temporal   the blamed service degrades no later than the other services on the claimed path
  path       every hop of the claimed propagation path is a real call edge, and the path ends at the frontend
"""
import re

from ..verify.claims import Check
from .topology import CALLERS, SERVICES

Z = 4.0                 # z-score above which a metric counts as anomalous
RESOURCE_Z = 20.0       # CPU or memory this far out means resource exhaustion, which network faults do not cause
TEMPORAL_SLACK = 1      # minutes of tolerance before a service counts as degrading earlier than the blamed one
BASELINE_MINUTES = 2    # minutes before the injection used for the statistics
SIGNATURE = {"cpu": ["cpu"], "mem": ["mem"], "disk": ["diskio"], "socket": ["socket"],
             "delay": ["latency-90"], "loss": ["error", "latency-90"]}
PATH_END = "frontend"


def norm(name):
    """Map a free-text service name onto the RCAEval service list, or None."""
    n = re.sub(r"[^a-z]", "", str(name).lower())
    for s in SERVICES:
        if n == s or n == s.replace("service", "") or (len(n) > 3 and s.startswith(n)):
            return s
    return None


def zscores(series, k0):
    base = [v for v in series[:k0] if v is not None]
    if len(base) < 3:   # absent before the incident: appearing at all is the anomaly
        return [0.0] * k0 + [0.0 if not v else 100.0 for v in series[k0:]]
    mu = sum(base) / len(base)
    sd = (sum((v - mu) ** 2 for v in base) / len(base)) ** 0.5
    scale = max(sd, 0.05 * abs(mu), 1e-6)
    return [0.0 if v is None else abs(v - mu) / scale for v in series]


def _first_hit(zs, k0):
    return next((k for k in range(k0, len(zs)) if zs[k] > Z), None)


def profile(features):
    """Per service: the largest post-injection z-score per metric, and the first minute any metric crosses Z."""
    k0 = features["inject_minute"] - BASELINE_MINUTES
    prof = {}
    for svc, metrics in features["metrics"].items():
        if not metrics:
            continue
        per, onset = {}, None
        for mt, series in metrics.items():
            vals = [v for v in series if v is not None]
            if not vals or max(vals) == min(vals):
                continue    # a flat series carries no information (often: the metric is not really collected)
            z = zscores(series, k0)
            per[mt] = max(z[k0:]) if len(z) > k0 else 0.0
            hit = _first_hit(z, k0) if mt != "workload" else None
            if hit is not None:
                onset = hit if onset is None else min(onset, hit)
        logs = features["error_logs"].get(svc)
        if logs:
            lz = zscores(logs, k0)
            per["logs"] = max(lz[k0:])
            hit = _first_hit(lz, k0)
            if hit is not None:
                onset = hit if onset is None else min(onset, hit)
        prof[svc] = {"z": per, "score": max([v for k, v in per.items() if k != "workload"] or [0]), "onset": onset}
    return prof


def baseline_rank(prof):
    """Statistical baseline (BARO-like): services ranked by their largest post-injection z-score."""
    return sorted(prof, key=lambda s: -prof[s]["score"])


def _signature_check(svc, fault, prof):
    p = prof[svc]
    if fault not in SIGNATURE:
        return Check("signature", False, f"claimed fault type '{fault}' is not one of cpu/mem/disk/delay/loss/socket.")
    if fault in ("delay", "loss"):
        hot = {m: p["z"].get(m, 0) for m in ("cpu", "mem") if p["z"].get(m, 0) > RESOURCE_Z}
        if hot:
            return Check("signature", False, f"claimed network {fault}, but {svc}'s own "
                         + ", ".join(f"{m} z={z:.0f}" for m, z in hot.items()) + " shows resource exhaustion, which network faults do not cause.")
        errs = [c for c in [svc] + CALLERS[svc] if prof.get(c, {}).get("z", {}).get("logs", 0) > Z]
        if fault == "delay" and errs:
            return Check("signature", False, f"claimed delay, but error logs surged at {', '.join(errs)}; pure delay produces "
                         "no errors (packet loss does).")
        zs = [prof[c]["z"].get(m, 0) for c in [svc] + CALLERS[svc] if c in prof for m in SIGNATURE[fault]]
        return Check("signature", bool(zs) and max(zs) > Z, f"claimed {fault}: latency/error z={max(zs or [0]):.0f} at {svc} "
                     "or its callers, with no resource exhaustion.")
    sig = [m for m in SIGNATURE[fault] if m in p["z"]]
    if sig:
        zmax = max(p["z"][m] for m in sig)
        return Check("signature", zmax > Z, f"claimed {fault}: {svc} {'/'.join(sig)} z={zmax:.1f}.")
    return Check("signature", None, f"{svc} does not export a {fault} metric; check not applicable.")


def _path_check(claim, svc, path):
    bad_hops = [f"{a}->{b}" for a, b in zip(path, path[1:]) if a and b and b not in CALLERS.get(a, [])]
    unknown = [x for x in claim.get("propagation_path", []) if norm(x) is None]
    ok = bool(path) and path[0] == svc and path[-1] == PATH_END and not bad_hops and not unknown
    if ok:
        return Check("path", True, "propagation path is a valid call chain to frontend.")
    problems = (["does not start at the blamed service"] if path and path[0] != svc else []) \
        + (["does not end at frontend"] if path and path[-1] != PATH_END else []) \
        + [f"no call edge {h}" for h in bad_hops] + [f"unknown component {u}" for u in unknown]
    return Check("path", False, f"invalid path {claim.get('propagation_path')}: " + ", ".join(problems))


def check(features, claim):
    """Claim checks for one answer. Returns (checks, blamed service or None, normalised path)."""
    prof = profile(features)
    svc = norm(claim.get("root_cause_service"))
    fault = str(claim.get("fault_type", "")).lower().strip()
    path = [norm(p) for p in claim.get("propagation_path", []) or []]
    if svc is None or svc not in prof:
        return [Check("evidence", False, f"Blamed component '{claim.get('root_cause_service')}' has no telemetry in this system.")], svc, path

    p = prof[svc]
    checks = [Check("evidence", p["score"] > Z, f"{svc} peak anomaly z={p['score']:.1f} (threshold {Z}).")]
    checks.append(_signature_check(svc, fault, prof))
    others = [s for s in path[1:] if s and s in prof and prof[s]["onset"] is not None]
    if p["onset"] is not None and others:
        later = [s for s in others if prof[s]["onset"] < p["onset"] - TEMPORAL_SLACK]
        checks.append(Check("temporal", not later, f"{svc} anomaly starts at minute {p['onset']}"
                            + (f", but {', '.join(later)} degraded earlier." if later else "; nothing on its path degraded earlier.")))
    checks.append(_path_check(claim, svc, path))
    return checks, svc, path
