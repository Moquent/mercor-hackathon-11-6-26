"""Typed extraction: Jev reads only the subject AI's answer and returns the claims the verifier can act on.

Jev never sees the telemetry or the answer key.
"""
import json
import re
from dataclasses import asdict, dataclass, field

from ..clients import jev
from ..config import JEV_THRESHOLD
from ..scenario.actions import ACTIONS
from ..scenario.topology import SERVICES


def parse_json_block(text):
    """The last fenced JSON object in an answer, or None."""
    matches = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    try:
        return json.loads(matches[-1]) if matches else None
    except json.JSONDecodeError:
        return None


@dataclass
class Extraction:
    json_block: dict | None
    jev_model: str
    blamed: str                       # Jev's single best root-cause choice
    roots: list                       # services named as root causes in the JSON block (or Jev's choice)
    blamed_confidence: float | None
    actions: dict = field(default_factory=dict)        # claimed catalog actions -> probability
    action_probs: dict = field(default_factory=dict)   # every catalog action -> probability

    def to_dict(self):
        return asdict(self)


def _state_text(answer, block):
    if block is None:
        return answer
    actions = "\n".join(f"- {a}" for a in block.get("actions", []))
    return (f"Root cause service: {block.get('root_cause_service')}\nMechanism: {block.get('mechanism')}\n"
            f"Recommended actions:\n{actions}")


def extract(answer, catalog=ACTIONS):
    block = parse_json_block(answer)
    questions = {"blamed": {"type": "choice",
                            "instructions": "Which single component does this diagnosis name as the root cause?",
                            "criteria": {s: s for s in SERVICES} | {"none": "no single component / unclear"}}}
    for key, action in catalog.items():
        questions[key] = {"type": "noul",
                          "instructions": f"Does the recommended remediation include this action: {action.description}?"}
    res = jev(_state_text(answer, block), questions)
    ans = res["answers"]
    actions = {k: ans[k]["noul"] for k in catalog if ans[k]["noul"] >= JEV_THRESHOLD}
    named = str((block or {}).get("root_cause_service", ""))
    roots = [s for s in SERVICES if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", named)]
    if not roots and ans["blamed"]["choice"] != "none":
        roots = [ans["blamed"]["choice"]]
    return Extraction(
        json_block=block, jev_model=res.get("model"), blamed=ans["blamed"]["choice"], roots=roots,
        blamed_confidence=ans["blamed"].get("confidence"), actions=actions,
        action_probs={k: round(ans[k]["noul"], 3) for k in catalog})
