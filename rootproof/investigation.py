"""Investigation-track evaluation: replay verdict plus step-by-step trace verification for every recorded investigation.

Also self-tests the trace verifier on hand-built traces whose steps come from the real tools, so their observations
are exactly what the AI would have seen.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from .agent.run import AGENT_INCIDENTS, load
from .agent.tools import Environment
from .scenario.actions import ALL_ACTIONS
from .scenario.incidents import NET_INCIDENTS
from .verify.replay import ground_truth
from .verify.trace import verify_trace
from .verify.verdict import verify

AGENT_RUNS = (1, 2, 3)


def _record(incident, calls, claims, root, actions):
    """A hand-built trace: each (tool, args) is executed against the incident's environment."""
    env = Environment(incident)
    steps = []
    for i, (tool, args) in enumerate(calls, 1):
        text, observed = env.call(tool, dict(args, reason="self-test"))
        steps.append({"id": f"s{i}", "tool": tool, "args": args, "output": text, "observed": observed})
    block = {"root_cause_service": root, "mechanism": "self-test", "actions": actions, "claims": claims}
    return {"task": incident.id, "run": 0, "steps": steps, "answer": "```json\n" + json.dumps(block) + "\n```"}


def selftest():
    """Expected labels for three traces on incident 16: one careful, two with the mistakes the verifier must catch."""
    inc = NET_INCIDENTS[0]
    careful = _record(inc, [("get_metrics", {"service": "player-core"}), ("check_health", {"service": "media-cache"}),
                            ("check_connectivity", {"src": "player-core", "dst": "media-cache"}), ("get_changes", {})],
                      [{"kind": "state", "service": "player-core", "state": "erroring", "evidence": ["s1"]},
                       {"kind": "state", "service": "media-cache", "state": "healthy", "evidence": ["s2"]},
                       {"kind": "edge", "src": "player-core", "dst": "media-cache", "type": "unreachable", "evidence": ["s3"]},
                       {"kind": "cause", "service": "player-core", "evidence": ["s3", "s4"]}],
                      "player-core", ["Roll back NetworkPolicy np-player-egress"])
    blame_unchecked = _record(inc, [("get_metrics", {"service": "player-core"}), ("get_changes", {})],
                              [{"kind": "state", "service": "media-cache", "state": "erroring", "evidence": ["s2"]},
                               {"kind": "edge", "src": "player-core", "dst": "media-cache", "type": "failure", "evidence": ["s1"]},
                               {"kind": "cause", "service": "media-cache", "evidence": ["s2"]}],
                              "media-cache", ["Roll back media-cache v3.2.0"])
    misread = _record(inc, [("check_health", {"service": "media-cache"}), ("get_metrics", {"service": "player-core"}),
                            ("get_changes", {})],
                      [{"kind": "state", "service": "session-api", "state": "erroring", "evidence": ["s1"]},
                       {"kind": "state", "service": "player-core", "state": "erroring", "evidence": ["s3"]}],
                      "player-core", ["Roll back NetworkPolicy np-player-egress"])
    cases = [("careful", careful, ["VERIFIED"] * 4),
             ("blames media-cache without checking it", blame_unchecked, ["FALSE", "FALSE", "FALSE"]),
             ("claims about a service it never looked at, and a citation that shows nothing", misread,
              ["UNGROUNDED", "UNSUPPORTED"])]
    out = []
    for name, rec, want in cases:
        got = [c["label"] for c in verify_trace(inc, rec)["claims"]]
        out.append({"case": name, "expected": want, "got": got, "pass": got == want})
    return out


def evaluate():
    jobs = [(inc, r) for inc in AGENT_INCIDENTS for r in AGENT_RUNS if load(inc.id, r)]

    def go(job):
        inc, r = job
        rec = load(inc.id, r)
        v = verify(inc, rec.get("answer") or "", ALL_ACTIONS)
        return {"task": inc.id, "run": r, "verdict": v.to_dict(), "trace": verify_trace(inc, rec),
                "steps": rec["steps"], "answer": rec.get("answer"), "final_reasoning": rec.get("final_reasoning", []),
                "model_reported": rec.get("model_reported"), "exchanges": len(rec["exchanges"])}

    with ThreadPoolExecutor(8) as ex:
        runs = list(ex.map(go, jobs))
    with ThreadPoolExecutor(8) as ex:
        truth = dict(zip([i.id for i in NET_INCIDENTS], ex.map(lambda i: ground_truth(i, ALL_ACTIONS), NET_INCIDENTS)))
    return runs, truth, selftest()


def stats(runs, packet_first):
    """Headline numbers for the investigation track. packet_first maps task -> packet-mode run-1 verdict."""
    first = [r for r in runs if r["run"] == 1]
    claims = [c for r in runs for c in r["trace"]["claims"]]
    n = max(1, len(claims))
    shared = [r for r in first if r["task"] in packet_first]
    return {
        "runs": len(runs), "tasks": len(first),
        "first_accept": sum(r["verdict"]["verdict"] == "ACCEPT" for r in first),
        "runs_accept": sum(r["verdict"]["verdict"] == "ACCEPT" for r in runs),
        "claims": len(claims),
        "claims_by_label": {k: sum(c["label"] == k for c in claims)
                            for k in ("VERIFIED", "UNSUPPORTED", "UNGROUNDED", "FALSE", "MALFORMED")},
        "verified_rate": round(sum(c["label"] == "VERIFIED" for c in claims) / n, 3),
        "false_rate": round(sum(c["label"] == "FALSE" for c in claims) / n, 3),
        "clean_traces": sum(r["trace"]["clean"] for r in runs),
        "right_fix_flawed_story": sum(r["verdict"]["fixed_in_replay"] and not r["trace"]["clean"] for r in runs),
        "mean_steps": round(sum(r["trace"]["n_steps"] for r in runs) / max(1, len(runs)), 1),
        "probed_connectivity": sum("check_connectivity" in r["trace"]["tools_used"] for r in runs),
        "with_missed_checks": sum(bool(r["trace"]["missed_checks"]) for r in runs),
        "packet_vs_agent_first_accept": {
            "packet": sum(packet_first[r["task"]] == "ACCEPT" for r in shared),
            "agent": sum(r["verdict"]["verdict"] == "ACCEPT" for r in shared), "tasks": len(shared)},
    }
