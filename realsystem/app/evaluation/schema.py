"""Model-output parsing. A parse failure is its own outcome; the raw answer is always preserved."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from app.verifier.claims import Claim


class ModelAnswer(BaseModel):
    claims: list[Claim]
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_answer(raw: str | None, schema: type[BaseModel] = ModelAnswer) -> tuple[BaseModel | None, str | None]:
    """Strict JSON. The only tolerance: a single markdown fence wrapping the whole response."""
    if not raw or not raw.strip():
        return None, "empty response"
    fenced = _FENCE.match(raw)
    text = fenced.group(1) if fenced else raw.strip()
    try:
        return schema.model_validate(json.loads(text)), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except ValidationError as exc:
        return None, f"schema violation: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
