"""Counterfactual replay: apply actions at page time in the same seeded simulation and judge the SLO."""
from itertools import combinations

from ..config import JUDGE_WINDOW, SLO_ERROR_RATE
from ..scenario.actions import ACTIONS
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY
from ..sim import error_rate, run


def ops_for(action_keys):
    return [op for key in action_keys for op in ACTIONS[key].ops]


def replay(incident, action_keys):
    """Returns (replay rows, entry error rate over the judged window)."""
    rows = run(topology_for(incident), incident.faults, ENTRY, ops_for(action_keys))
    return rows, error_rate(rows, ENTRY, *JUDGE_WINDOW)


def resolves(incident, action_keys):
    return replay(incident, action_keys)[1] < SLO_ERROR_RATE


def ground_truth(incident):
    """Minimal sufficient action sets: every single executable action, else every pair."""
    keys = [k for k, a in ACTIONS.items() if a.ops]
    singles = [(k,) for k in keys if resolves(incident, [k])]
    if singles:
        return singles
    return [pair for pair in combinations(keys, 2) if resolves(incident, list(pair))]


def min_scale(incident, action_keys, svc):
    """Smallest capacity multiplier (in 0.5 steps) for svc that ends the incident, with the other actions applied."""
    scale_prefix = f"scale_{svc.replace('-', '_')}"
    others = [k for k in action_keys if not k.startswith(scale_prefix)]
    for k in [x / 2 for x in range(2, 41)]:
        rows = run(topology_for(incident), incident.faults, ENTRY, ops_for(others) + [("scale", svc, k)])
        if error_rate(rows, ENTRY, *JUDGE_WINDOW) < SLO_ERROR_RATE:
            return k
    return None

