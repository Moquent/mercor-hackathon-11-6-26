# RootProof Live: HTTP API and event schema

Server: `python3 -m rootproof serve [--host 127.0.0.1] [--port 8080]` (stdlib only). The console is served at `/`
from `rootproof/live/assets/` (`index.html`, `live.css`, `live.js`; no CDN, no build step).

## REST
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/catalog` | | `{topology, incidents, runs, tools, fault_modes}` (below) |
| GET | `/api/sim/<incident_id>` | | `{incident_id, t: [0..599], page_t: 300, services: {svc: {rps:[], lat_ms:[], err:[]}}, links: [{src,dst,mode,value,start,end}], faults: [...], truth: "text", events: [[t,svc,kind,msg]], ground_truth: [[action,...],...]}` (arrays are per second, rounded to 3 decimals) |
| POST | `/api/replay` | `{"task": "16-link-partition", "run": 1, "speed": 1.0}` | `{"job": id}`: replays a saved investigation's steps over SSE (about 0.6 s per step at speed 1), then runs live verification |
| POST | `/api/investigate` | `{"task": id}` | `{"job": id}`: a NEW live agent run (real API calls), then verification. Saved as `attempts/agent/live/<task>.<job>.json` |
| POST | `/api/verify` | `{"task": id, "run": 1}` | `{"job": id}`: verification only, for a saved run |
| POST | `/api/bench` | `{"track": "investigation"}` | `{"job": id}`: re-verifies every saved investigation, streaming rows |
| POST | `/api/draft` | `{"description": "free text"}` | `{"job": id}`: the AI drafts a case, the server validates and simulates it, then emits `case_ready` |
| GET | `/api/stream/<job>` | | `text/event-stream`. Every message is `data: <json>\n\n` with the shape `{"job","seq","ts","type", ...payload}`. The stream ends after the event `{"type":"done"}` |

### catalog
```
topology: {entry: "edge-gw", nodes: [{id, deps:[...], mode, retries, timeout_ms, capacity}], jobs: {"sub-poller":"docstore","msg-cleanup":"notify-relay"}}
incidents: [{id, title, kind: "packet+agent"|"agent"|"custom", no_outage}]
runs: [{task, run, verdict, n_steps, counts:{VERIFIED,UNSUPPORTED,UNGROUNDED,FALSE,MALFORMED}}]
tools: [{name, description}]
fault_modes: ["err","capacity","extra_rps","link:refuse","link:dns","link:tls","link:latency","link:loss"]
```

## Event types (the `type` field)
Agent phase:
- `job_started` `{kind: "replay"|"investigate"|"verify"|"bench"|"draft", task, run}`
- `request_sent` `{exchange, n_input_items}`
- `reasoning` `{exchange, text}` (a reasoning summary, possibly empty)
- `tool_call` `{step, tool, args, reason, focus: {nodes:[...], edge:[src,dst]|null}}`: `focus` says what the agent is looking at, for the graph cursor
- `tool_result` `{step, tool, output, observed}`
- `response` `{exchange, usage: {input_tokens, output_tokens, ...}, latency_ms}`
- `answer` `{text, json}` (json = the parsed final block or null)

Verification phase (fixed order):
- `stage` `{name: "extract"|"replay"|"story"|"claims"|"missed"|"verdict", status: "start"|"done"}`
- `extract` `{actions: {key: prob}, claimed: [key...], roots: [svc...], blamed}`
- `replay_seed` `{seed, fixed: bool, err_after: float, curve: [[t, entry_err], ...]}` (every 5 s, t 60..600, AI actions applied at 300)
- `replay_baseline` `{curve: [[t, err], ...]}` (no action) and `{"truth_curve": ...}` (computed fix, if any)
- `invariant` `{check, pass, detail}`
- `claim` `{index, text, claim, grounded, supported, true, label, evidence:[step ids], commit_step}`
- `missed_check` `{tool, args, would_show}`
- `first_wrong` `{step, claim, label, would_have_caught_it}`
- `verdict` `{verdict, fixed_in_replay, fixed_by_seed:[...], ai_error_rate_after, clean_trace, counts}`

Benchmark:
- `bench_row` `{task, run, verdict, counts, clean}`
- `bench_totals` `{done, total, accept, claims, verified, false, clean}`

Case composer:
- `draft_text` `{text}` (the AI's raw draft)
- `case_ready` `{case: {id, title, faults, events, logs, truth}, ground_truth: [[...]], entry_err_before: float}` or `case_error` `{error}`

Always:
- `log` `{level: "info"|"warn"|"error", msg}`
- `error` `{msg}`
- `done` `{}`
