"""Seeded fluid-queue simulator of a service DAG, with fault injection and counterfactual replay.

Each tick (1 s) computes offered load top-down (retries and fallbacks multiply it), then queueing, latency and
failure bottom-up. Failure seen by a caller = the callee's own errors, or latency past the caller's timeout.
"""
import random
from dataclasses import dataclass, field, replace

T = 600
PAGE_T = 300                 # the on-call is paged here; interventions apply from here
JUDGE = (450, 600)           # SLO judged on this window of the replay
SLO_OK = 0.05                # incident is resolved if the entry error rate stays below this


@dataclass
class Svc:
    name: str
    capacity: float
    base_ms: float
    deps: list = field(default_factory=list)
    mode: str = "all"            # "all": every dep is required; "fallback": try deps in order until one succeeds
    timeout_ms: float = 1000.0
    retries: int = 0
    err: float = 0.0
    queue: bool = True           # False = rate limiter: excess is rejected immediately (429) instead of queued
    extra_rps: float = 0.0       # background load not coming from callers (cron jobs, pollers)
    sticky: bool = False         # its own errors are deterministic per request: a retry hits the same error


@dataclass
class Fault:
    id: str
    svc: str
    field: str
    value: float
    start: int
    end: int = 10 ** 9
    kind: str = "transient"      # transient: cleared by restart | config: cleared by rollback | external: by restore
    change: str = ""             # change owner a rollback targets (deploy/job name)


def _order(svcs, entry):
    seen, out = set(), []

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        out.append(n)
        for c in svcs[n].deps:
            visit(c)
    visit(entry)
    for n in svcs:
        visit(n)
    return out


def run(topology, faults, entry, actions=(), seed=0, rps=200.0, t_end=T):
    """actions: list of ops applied from PAGE_T on:
       ("set", svc, field, value) | ("scale", svc, k) | ("restart", svc) | ("rollback", change) | ("restore", svc)"""
    rng = random.Random(seed)
    order = _order(topology, entry)
    parents = {n: [p for p in order if n in topology[p].deps] for n in order}
    backlog = {n: 0.0 for n in order}
    p_fail = {n: 0.0 for n in order}
    p_sticky = {n: 0.0 for n in order}
    lat = {n: topology[n].base_ms for n in order}
    cleared, restarted_at = set(), {}
    rows = []
    for t in range(t_end):
        if t == PAGE_T:
            for op in actions:
                if op[0] == "rollback":
                    cleared |= {f.id for f in faults if f.kind == "config" and f.change == op[1]}
                elif op[0] == "restore":
                    cleared |= {f.id for f in faults if f.kind == "external" and f.svc == op[1]}
                elif op[0] == "restart":
                    restarted_at[op[1]] = t
                    backlog[op[1]] = 0.0
        svcs = {n: replace(s, deps=list(s.deps)) for n, s in topology.items()}
        for f in faults:
            if f.start <= t < f.end and f.id not in cleared:
                if f.kind == "transient" and f.svc in restarted_at and t >= restarted_at[f.svc]:
                    continue
                setattr(svcs[f.svc], f.field, f.value)
        if t >= PAGE_T:
            for op in actions:
                if op[0] == "set":
                    setattr(svcs[op[1]], op[2], op[3])
                elif op[0] == "scale":
                    svcs[op[1]].capacity *= op[2]
        offered = {n: svcs[n].extra_rps for n in order}
        offered[entry] += rps * (1 + 0.04 * rng.gauss(0, 1))
        for n in order:
            s = svcs[n]
            carry = offered[n]
            for c in s.deps:
                p = _retryable(p_fail[c], p_sticky[c])
                attempts = sum(p ** k for k in range(s.retries + 1))
                offered[c] += carry * attempts
                if s.mode == "fallback":
                    carry *= _fail_after_retries(p_fail[c], p_sticky[c], s.retries)
        p_self = {}
        for n in reversed(order):
            s = svcs[n]
            if s.queue:
                served = min(offered[n] + backlog[n], s.capacity)
                backlog[n] = min(max(0.0, backlog[n] + offered[n] - s.capacity), s.capacity * 5)
                wait_ms = 1000 * backlog[n] / s.capacity
                reject = 0.0
            else:
                served, wait_ms = min(offered[n], s.capacity), 0.0
                reject = max(0.0, 1 - s.capacity / offered[n]) if offered[n] > 0 else 0.0
            own_ms = s.base_ms / max(0.05, 1 - min(served / s.capacity, 0.95))
            if s.mode == "fallback":
                miss, child_ms = 1.0, 0.0
                for c in s.deps:
                    child_ms += miss * min(lat[c], s.timeout_ms)
                    miss *= _fail_after_retries(p_fail[c], p_sticky[c], s.retries)
                ok = (1 - s.err) * (1 - reject) * (1 - miss)
            else:
                child_ms = sum(min(lat[c], s.timeout_ms) for c in s.deps)
                ok = (1 - s.err) * (1 - reject)
                for c in s.deps:
                    ok *= 1 - _fail_after_retries(p_fail[c], p_sticky[c], s.retries)
            lat[n] = own_ms + wait_ms + child_ms
            p_self[n] = 1 - ok
        for n in order:
            to = min([svcs[p].timeout_ms for p in parents[n]] or [1e9])
            p_to = min(1.0, (lat[n] - to) / (0.25 * to)) if lat[n] > to else 0.0
            p_fail[n] = min(1.0, 1 - (1 - p_self[n]) * (1 - p_to))
            if svcs[n].sticky:
                p_sticky[n] = p_self[n]
            elif svcs[n].mode == "all":
                st_ok = 1.0
                for c in svcs[n].deps:
                    st_ok *= 1 - p_sticky[c]
                p_sticky[n] = min(p_fail[n], 1 - st_ok)
            else:
                p_sticky[n] = 0.0
            noise = 1 + 0.02 * rng.gauss(0, 1)
            rows.append({"t": t, "svc": n, "rps": offered[n] * noise, "lat_ms": lat[n] * noise,
                         "err": p_self[n], "seen_fail": p_fail[n], "backlog": backlog[n]})
    return rows


def _retryable(p, p_st):
    """Share of calls failing with a retryable error (timeouts, 5xx). Sticky 4xx-style errors are not retried."""
    if p_st >= 1.0:
        return 0.0
    return max(0.0, 1 - (1 - p) / (1 - p_st))


def _fail_after_retries(p, p_st, retries):
    return 1 - (1 - p_st) * (1 - _retryable(p, p_st) ** (retries + 1))


def error_rate(rows, svc, t0, t1):
    xs = [r["err"] for r in rows if r["svc"] == svc and t0 <= r["t"] < t1]
    return sum(xs) / len(xs)


def resolved(rows, entry):
    return error_rate(rows, entry, *JUDGE) < SLO_OK
