"""The verdict: ACCEPT, PARTIAL or REJECT for one answer on one incident.

ACCEPT   the actions end the incident in replay and the blamed root passes every claim check
PARTIAL  the actions work but the story is wrong, or the story holds but the actions don't end the incident
REJECT   the actions don't end the incident and the story is contradicted
No-outage incidents are different: any production action is a REJECT.
"""
from dataclasses import dataclass, field

from ..config import JUDGE_WINDOW, PAGE_T, SLO_ERROR_RATE
from ..scenario.actions import ACTIONS
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY
from ..sim import error_rate, run
from ..timeline import clock
from .claims import Check, amplification, invariants
from .extract import Extraction, extract
from .replay import min_scale, replay

ROLLBACK_TARGET = {"rollback_session_api": "session-api", "rollback_notify_relay": "notify-relay",
                   "rollback_meta_index": "meta-index", "rollback_media_cache": "media-cache",
                   "rollback_sub_poller": "sub-poller"}


@dataclass
class Verdict:
    task: str
    verdict: str
    fixed_in_replay: bool
    ai_error_rate_after: float
    extraction: Extraction
    invariants: list = field(default_factory=list)
    certificates: list = field(default_factory=list)
    incident_onset: str | None = None

    def to_dict(self):
        return {"task": self.task, "verdict": self.verdict, "fixed_in_replay": self.fixed_in_replay,
                "ai_error_rate_after": self.ai_error_rate_after, "extraction": self.extraction.to_dict(),
                "invariants": [c.to_dict() for c in self.invariants], "certificates": self.certificates,
                "incident_onset": self.incident_onset}


def _no_outage_verdict(incident, ex, rows_noop, err_ai):
    base_err = error_rate(rows_noop, ENTRY, 0, PAGE_T)
    prod_actions = [a for a in ex.actions if ACTIONS[a].ops]
    certs = []
    if prod_actions:
        certs.append(f"There is no outage to fix: {ENTRY} error rate is {base_err:.1%} across the whole window, and request rates "
                     f"and the independent playback_sessions insert rate are flat. The answer still changes production: "
                     f"{', '.join(prod_actions)}.")
    checks = [Check("impact", not prod_actions, "No user-facing impact exists in the telemetry.")]
    return Verdict(task=incident.id, verdict="REJECT" if prod_actions else "ACCEPT", fixed_in_replay=True,
                   ai_error_rate_after=round(err_ai, 3), extraction=ex, invariants=checks, certificates=certs)


def verify(incident, answer):
    ex = extract(answer)
    rows_noop = run(topology_for(incident), incident.faults, ENTRY)
    _, err_ai = replay(incident, list(ex.actions))
    if incident.no_outage:
        return _no_outage_verdict(incident, ex, rows_noop, err_ai)

    fixed = err_ai < SLO_ERROR_RATE
    checks, t_inc = invariants(incident, rows_noop, ex.roots)
    story_ok = all(c.passed for c in checks)
    verdict = "ACCEPT" if fixed and story_ok else "PARTIAL" if fixed or story_ok else "REJECT"

    certs = []
    if not fixed:
        certs += _replay_certificates(incident, ex, rows_noop, err_ai, t_inc)
    certs += [c.detail for c in checks if not c.passed]
    return Verdict(task=incident.id, verdict=verdict, fixed_in_replay=fixed, ai_error_rate_after=round(err_ai, 3),
                   extraction=ex, invariants=checks, certificates=certs,
                   incident_onset=clock(t_inc) if t_inc is not None else None)


def _replay_certificates(incident, ex, rows_noop, err_ai, t_inc):
    certs = [f"Replay with the AI's actions applied at {clock(PAGE_T)}: {ENTRY} error rate stays at {err_ai:.0%} "
             f"over {clock(JUDGE_WINDOW[0])}-{clock(JUDGE_WINDOW[1])} (SLO {SLO_ERROR_RATE:.0%})."]
    if not ex.actions:
        certs.append("No executable action was recognised in the answer.")
    svc, ratio = amplification(incident, rows_noop)
    if ratio > 2 and not any("retries" in a for a in ex.actions):
        certs.append(f"{svc} is receiving {ratio:.1f}x its normal request rate from layered retries; "
                     f"the proposed actions leave that amplification in place.")
    for a in ex.actions:
        if a.startswith("scale_") or a.startswith("restart_media"):
            target = "media-cache" if "media_cache" in a else "docstore" if "docstore" in a else "brio-origin"
            k = min_scale(incident, list(ex.actions), target)
            certs.append(f"Scaling {target} would need >= {k}x capacity to end the incident." if k else
                         f"No amount of {target} scaling (up to 20x) ends the incident.")
    for a in ex.actions:
        target = ROLLBACK_TARGET.get(a)
        for t, svc, kind, msg in incident.events:
            if svc == target and kind in ("deploy", "config") and t_inc is not None and t > t_inc:
                certs.append(f"Temporal: the {svc} change you roll back ('{msg[:48]}') landed at {clock(t)}, "
                             f"after {ENTRY} errors began at {clock(t_inc)}; it cannot be the cause.")
    return certs
