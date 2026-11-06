"""The investigation loop. The subject AI calls tools until it answers; every request, raw response and step is saved.

attempts/agent/<id>.run<k>.json holds the full record. Nothing in it is edited after the run; run 1 is the official
first attempt.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..clients import _env, _post
from ..config import AGENT_ATTEMPTS, PAGE_T, SUBJECT_MODEL
from ..scenario.incidents import INCIDENTS, NET_INCIDENTS
from ..timeline import clock
from ..verify.extract import parse_json_block
from .tools import SCHEMAS, Environment

MAX_TOOL_CALLS = 20
AGENT_INCIDENTS = INCIDENTS + NET_INCIDENTS

PROMPT = """You are the on-call SRE for Larkwave, a music streaming service. You were paged at {paged}: listeners are seeing errors.
Investigate with the tools (each call needs a one-sentence reason), find the root cause, and say exactly what the on-call
should do right now to restore service. If more than one action is needed, list every one, with exact values.
Every tool result starts with a step id like [s3]. Only claim what you have checked; cite the step ids that show it.

End your answer with a JSON block exactly like:
```json
{{"root_cause_service": "...", "mechanism": "...", "actions": ["...", "..."],
  "claims": [
    {{"kind": "state", "service": "<service>", "state": "erroring|slow|overloaded|healthy", "evidence": ["s1"]}},
    {{"kind": "edge", "src": "<caller>", "dst": "<callee>", "type": "failure|load|unreachable|dns|tls|latency|loss", "evidence": ["s2"]}},
    {{"kind": "cause", "service": "<root cause service>", "evidence": ["s3", "s4"]}}
  ]}}
```
A state claim says what a service is doing; an edge claim says how one service's problem reaches its caller
("failure": the callee's errors or slowness, "load": the caller's extra traffic overloads the callee, "unreachable": the
caller cannot connect although the callee may be fine, "dns": name resolution fails, "tls": the handshake or certificate
fails, "latency": the link itself adds delay and calls time out, "loss": packet loss, resets and retransmits). Include one claim for every service and link your diagnosis relies on."""

URL = "https://api.openai.com/v1/responses"


def attempt_path(task_id, run_no):
    return os.path.join(AGENT_ATTEMPTS, f"{task_id}.run{run_no}.json")


def load(task_id, run_no):
    path = attempt_path(task_id, run_no)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(raw):
    return "".join(c.get("text", "") for o in raw.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") == "output_text")


def _reasoning(raw):
    return [s.get("text", "") for o in raw.get("output", []) if o.get("type") == "reasoning" for s in o.get("summary", [])]


def _focus(tool, args):
    """What a tool call looks at, for the live graph cursor."""
    a = args if isinstance(args, dict) else {}
    if a.get("src") and a.get("dst"):
        return {"nodes": [a["src"], a["dst"]], "edge": [a["src"], a["dst"]]}
    return {"nodes": [a["service"]] if a.get("service") else [], "edge": None}


def investigate(incident, run_no, model=SUBJECT_MODEL, emit=None, path=None):
    """emit(type, **payload) streams every step live; path overrides where the record is saved."""
    emit = emit or (lambda *a, **k: None)
    path = path or attempt_path(incident.id, run_no)
    if os.path.exists(path):
        return path
    env = Environment(incident)
    prompt = PROMPT.format(paged=clock(PAGE_T))
    record = {"task": incident.id, "run": run_no, "model_requested": model, "started_at_utc": _now(), "prompt": prompt,
              "tools": SCHEMAS, "max_tool_calls": MAX_TOOL_CALLS, "exchanges": [], "steps": []}
    body = {"model": model, "input": prompt, "tools": SCHEMAS, "reasoning": {"summary": "auto"}}
    while True:
        sent_at = _now()
        exchange = len(record["exchanges"])
        emit("request_sent", exchange=exchange, n_input_items=1 if isinstance(body["input"], str) else len(body["input"]))
        t0 = time.time()
        raw = _post(URL, _env["OPENAI"], body)
        emit("response", exchange=exchange, usage=raw.get("usage"), latency_ms=round(1000 * (time.time() - t0)))
        record["exchanges"].append({"sent_at_utc": sent_at, "request": body, "response": raw})
        record["model_reported"] = raw.get("model")
        thoughts = _reasoning(raw)
        for text in thoughts:
            emit("reasoning", exchange=exchange, text=text)
        calls = [o for o in raw.get("output", []) if o.get("type") == "function_call"]
        if not calls:
            record["answer"] = _text(raw)
            record["final_reasoning"] = thoughts
            emit("answer", text=record["answer"], json=parse_json_block(record["answer"]))
            break
        outputs = []
        for c in calls:
            sid = f"s{len(record['steps']) + 1}"
            try:
                args = json.loads(c["arguments"])
                emit("tool_call", step=sid, tool=c["name"], args=args, reason=args.get("reason"), focus=_focus(c["name"], args))
                text, observed = env.call(c["name"], args)
            except Exception as e:   # malformed call: the AI sees the error, the record keeps it
                args, text, observed = c.get("arguments"), f"tool error: {e}", {"error": str(e)}
            record["steps"].append({"id": sid, "exchange": len(record["exchanges"]) - 1, "tool": c["name"], "args": args,
                                    "reason": args.get("reason") if isinstance(args, dict) else None,
                                    "reasoning_summary": thoughts, "output": text, "observed": observed})
            emit("tool_result", step=sid, tool=c["name"], output=text, observed=observed)
            thoughts = []
            outputs.append({"type": "function_call_output", "call_id": c["call_id"], "output": f"[{sid}] {text}"})
        body = {"model": model, "previous_response_id": raw["id"], "input": outputs, "tools": SCHEMAS,
                "reasoning": {"summary": "auto"}}
        if len(record["steps"]) >= MAX_TOOL_CALLS:
            body["tool_choice"] = "none"
            body["input"] = outputs + [{"role": "user", "content": "Tool budget used up. Give your final answer now."}]
    record["finished_at_utc"] = _now()
    record["usage"] = [x["response"].get("usage") for x in record["exchanges"]]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=1)
    return path


def investigate_all(runs=1, incidents=AGENT_INCIDENTS, workers=8):
    jobs = [(inc, r) for r in range(1, runs + 1) for inc in incidents]
    with ThreadPoolExecutor(workers) as ex:
        for path in ex.map(lambda j: _safe(*j), jobs):
            print(path, flush=True)


def _safe(incident, run_no):
    try:
        return investigate(incident, run_no)
    except Exception as e:
        return f"FAILED {incident.id} run{run_no}: {e}"
