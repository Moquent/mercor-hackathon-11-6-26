"""Every incident the console can run: the built-in ones plus cases drafted in the composer (cases/custom/*.json)."""
import json
import os
import re

from ..config import ROOT
from ..scenario.incidents import INCIDENTS, NET_INCIDENTS, Incident
from ..sim import Fault

CUSTOM_DIR = os.path.join(ROOT, "cases", "custom")


def _from_json(d):
    faults = [Fault(**f) for f in d["faults"]]
    events = [tuple(e) for e in d.get("events", [])]
    return Incident(id=d["id"], title=d["title"], faults=faults, events=events, logs=d.get("logs", {}),
                    truth=d.get("truth", ""), no_outage=d.get("no_outage", False))


def custom():
    out = []
    if os.path.isdir(CUSTOM_DIR):
        for name in sorted(os.listdir(CUSTOM_DIR)):
            if name.endswith(".json"):
                with open(os.path.join(CUSTOM_DIR, name)) as f:
                    out.append(_from_json(json.load(f)))
    return out


def all_incidents():
    return INCIDENTS + NET_INCIDENTS + custom()


def find(task_id):
    return next((i for i in all_incidents() if i.id == task_id), None)


def save(case):
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    base = re.sub(r"[^a-z0-9-]", "-", case["id"].lower())[:48]
    for number in range(10000):
        case["id"] = base if number == 0 else f"{base[:43]}-{number}"
        try:
            with open(os.path.join(CUSTOM_DIR, case["id"] + ".json"), "x") as f:
                json.dump(case, f, indent=1)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"no unused case id for {base}")
    return find(case["id"])
