"""Claim checks: computed from the telemetry the AI saw, they explain why an answer is wrong.

Checks are about the blamed component: is it on the request path, was it already failing before the incident
(baseline), did it degrade first (temporal), is it a victim of an upstream failure or of fallback load (origin), and
does a retry storm amplify its load (amplification).
"""
from dataclasses import dataclass

from ..config import PAGE_T
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY
from ..timeline import clock

# Jobs are not services: their load lands on the service listed here.
LOAD_SOURCES = {"sub-poller": "docstore", "msg-cleanup": "notify-relay"}
BASELINE_SECONDS = 90
ERROR_JUMP = 0.05
TEMPORAL_SLACK_S = 15


@dataclass
class Check:
    name: str
    passed: bool | None
    detail: str

    def to_dict(self):
        return {"check": self.name, "pass": self.passed, "detail": self.detail}


def series(rows, svc, key):
    return {r["t"]: r[key] for r in rows if r["svc"] == svc}


def onset(rows, svc):
    """First second after the baseline when the service's errors, latency or load depart from normal."""
    err, lat, rps = (series(rows, svc, k) for k in ("err", "lat_ms", "rps"))
    base_err = sum(err[t] for t in range(BASELINE_SECONDS)) / BASELINE_SECONDS
    base_lat = sum(lat[t] for t in range(BASELINE_SECONDS)) / BASELINE_SECONDS
    base_rps = sum(rps[t] for t in range(BASELINE_SECONDS)) / BASELINE_SECONDS
    for t in range(BASELINE_SECONDS, PAGE_T):
        if err[t] > base_err + ERROR_JUMP or lat[t] > 2 * base_lat or rps[t] > 1.5 * base_rps + 5:
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


def _root_checks(incident, rows, blamed, t_inc, first):
    topo = topology_for(incident)
    svc = LOAD_SOURCES.get(blamed, blamed)
    checks = []

    on_path = svc in reachable(topo)
    checks.append(Check("reachability", on_path,
                        f"{blamed} is on the listener request path." if on_path else
                        f"{blamed} is not on any path from {ENTRY}; it cannot cause {ENTRY} errors."))

    err = series(rows, svc, "err")
    pre = sum(err[t] for t in range(BASELINE_SECONDS)) / BASELINE_SECONDS
    during = sum(err[t] for t in range(t_inc or 0, PAGE_T)) / max(1, PAGE_T - (t_inc or 0))
    pre_existing = pre > 0.05 and abs(during - pre) < 0.05
    checks.append(Check("baseline", not pre_existing,
                        f"{blamed} error rate was already {pre:.0%} before the incident and is {during:.0%} now: pre-existing noise."
                        if pre_existing else f"{blamed} changed during the incident (err {pre:.0%} -> {during:.0%})."))

    t_svc = onset(rows, svc)
    if t_svc is None:
        checks.append(Check("temporal", False, f"{blamed} shows no anomaly at all before the page."))
    elif t_svc == first:
        ok = t_inc is None or t_svc <= t_inc + TEMPORAL_SLACK_S
        checks.append(Check("temporal", ok,
                            f"{blamed} degraded at {clock(t_svc)}; {ENTRY} errors began at {clock(t_inc)}."
                            + ("" if ok else " Effect precedes the claimed cause; something else started the incident.")))

    earlier = [c for c in topo[svc].deps
               if (oc := onset(rows, c)) is not None and t_svc is not None and oc <= t_svc and series(rows, c, "err")[PAGE_T] > 0.2]
    parents = [p for p in topo if svc in topo[p].deps and topo[p].mode == "fallback"]
    shifted = []
    for p in parents:
        chain = topo[p].deps
        shifted = [c for c in chain[:chain.index(svc)] if series(rows, c, "err")[PAGE_T] > 0.5]
    victim = bool(earlier or shifted)
    why = (f"its failures follow {', '.join(earlier)} which failed first" if earlier else
           f"its load came from fallback traffic after {', '.join(shifted)} failed" if shifted else "")
    checks.append(Check("origin", not victim,
                        f"{blamed} is a victim: {why}." if victim else f"{blamed} is an origin, not a downstream victim."))
    return checks


def invariants(incident, rows, roots):
    """Checks for every named root cause. A root that fails a non-temporal check is kept as a warning when another root passes."""
    t_inc = onset(rows, ENTRY)
    roots = [r for r in roots if r != "none" and LOAD_SOURCES.get(r, r) in topology_for(incident)]
    if not roots:
        return [Check("claim", False, "No root-cause component could be identified in the answer.")], t_inc

    starts = [onset(rows, LOAD_SOURCES.get(r, r)) for r in roots]
    first = min((t for t in starts if t is not None), default=None)
    per_root = {r: _root_checks(incident, rows, r, t_inc, first) for r in roots}
    clean = [r for r, cs in per_root.items() if all(c.passed for c in cs if c.name != "temporal")]

    checks = []
    for r, cs in per_root.items():
        for c in cs:
            soft = not c.passed and c.name != "temporal" and clean and r not in clean
            if soft:
                checks.append(Check(f"{c.name} (extra root, warning)", True, c.detail))
            else:
                checks.append(c)
    if not clean:
        checks.append(Check("claim", False, "No named root cause survives the origin/reachability/baseline checks."))
    return checks, t_inc


def amplification(incident, rows):
    """The service whose load grew most at page time versus the baseline, and the growth factor.

    Ties go to the service closest to the entry point, so the retry source is named rather than its target.
    """
    rps = {r["svc"]: r["rps"] for r in rows if r["t"] == PAGE_T}
    base = {r["svc"]: r["rps"] for r in rows if r["t"] == 60}
    ratios = {s: rps[s] / base[s] for s in rps if base[s] > 20}
    top = max(ratios.values())
    topo = topology_for(incident)
    depth = {}
    for n in [ENTRY] + list(topo):
        for c in topo[n].deps:
            depth[c] = depth.get(n, 0) + 1
    svc = min((s for s, r in ratios.items() if r >= 0.9 * top), key=lambda s: depth.get(s, 0))
    return svc, ratios[svc]
