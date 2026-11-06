"""Trace context propagated explicitly over HTTP headers and Redis message fields."""
from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

H_TRACE, H_REQUEST, H_PARENTS, H_RUN = "x-trace-id", "x-request-id", "x-parent-event-ids", "x-run-id"
H_MESSAGE, H_ATTEMPT, H_ATTEMPT_NO, H_KIND = "x-message-id", "x-delivery-attempt-id", "x-attempt-no", "x-message-kind"


class TraceContext(BaseModel):
    trace_id: str
    request_id: str | None = None
    run_id: str = "adhoc"
    label: str = ""
    parent_event_ids: list[str] = []
    message_id: str | None = None
    delivery_attempt_id: str | None = None
    attempt_no: int | None = None
    message_kind: str | None = None

    def headers(self, parents: list[str]) -> dict[str, str]:
        out = {
            H_TRACE: self.trace_id,
            H_REQUEST: self.request_id or "",
            H_PARENTS: ",".join(parents),
            H_RUN: self.run_id,
            "x-label": self.label,
        }
        if self.message_id:
            out[H_MESSAGE] = self.message_id
        if self.delivery_attempt_id:
            out[H_ATTEMPT] = self.delivery_attempt_id
            out[H_ATTEMPT_NO] = str(self.attempt_no)
        if self.message_kind:
            out[H_KIND] = self.message_kind
        return out

    @classmethod
    def from_headers(cls, h: Mapping[str, str]) -> "TraceContext":
        attempt_no = h.get(H_ATTEMPT_NO)
        return cls(
            trace_id=h[H_TRACE],
            request_id=h.get(H_REQUEST) or None,
            run_id=h.get(H_RUN, "adhoc"),
            label=h.get("x-label", ""),
            parent_event_ids=[p for p in h.get(H_PARENTS, "").split(",") if p],
            message_id=h.get(H_MESSAGE),
            delivery_attempt_id=h.get(H_ATTEMPT),
            attempt_no=int(attempt_no) if attempt_no else None,
            message_kind=h.get(H_KIND),
        )
