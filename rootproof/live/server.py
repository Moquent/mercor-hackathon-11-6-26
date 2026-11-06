"""Local HTTP server for the console: static assets, a small JSON API, and Server-Sent Events per job (stdlib only)."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..agent.run import load
from ..agent.tools import SCHEMAS
from ..config import RESULTS
from ..scenario.topology import ENTRY, topology
from . import bus, cases, stream

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8"}
_sim_cache = {}


def catalog():
    topo = topology()
    nodes = [{"id": n, "deps": s.deps, "mode": s.mode, "retries": s.retries, "timeout_ms": s.timeout_ms,
              "capacity": s.capacity} for n, s in topo.items()]
    from ..scenario.incidents import INCIDENTS
    packet_ids = {i.id for i in INCIDENTS}
    incidents = [{"id": i.id, "title": i.title, "no_outage": i.no_outage,
                  "kind": "custom" if i.id.startswith("x-") else "packet+agent" if i.id in packet_ids else "agent"}
                 for i in cases.all_incidents()]
    saved = {}
    try:
        with open(os.path.join(RESULTS, "summary.json")) as f:
            for r in json.load(f).get("investigation", {}).get("runs", []):
                saved[(r["task"], r["run"])] = r
    except (OSError, ValueError):
        pass
    runs = []
    for i in cases.all_incidents():
        for r in (1, 2, 3):
            rec = load(i.id, r)
            if rec:
                s = saved.get((i.id, r))
                runs.append({"task": i.id, "run": r, "n_steps": len(rec["steps"]), "model": rec.get("model_reported"),
                             "verdict": s and s["verdict"]["verdict"], "counts": s and s["trace"]["counts"]})
    return {"topology": {"entry": ENTRY, "nodes": nodes, "jobs": {"sub-poller": "docstore", "msg-cleanup": "notify-relay"}},
            "incidents": incidents, "runs": runs,
            "tools": [{"name": s["name"], "description": s["description"]} for s in SCHEMAS],
            "fault_modes": ["err", "capacity", "extra_rps", "link:refuse", "link:dns", "link:tls", "link:latency", "link:loss"]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # keep the terminal quiet; the event log is the record
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        if path.startswith("/api/dag/"):
            from ..dagscope.routes import handle_get
            result = handle_get(path)
            if result is not None:
                return self._send(*result)
        if path == "/api/catalog":
            return self._send(200, catalog())
        if path.startswith("/api/sim/"):
            inc = cases.find(path.rsplit("/", 1)[1])
            if inc is None:
                return self._send(404, {"error": "unknown incident"})
            if inc.id not in _sim_cache:
                _sim_cache[inc.id] = stream.sim_payload(inc)
            return self._send(200, _sim_cache[inc.id])
        if path.startswith("/api/stream/"):
            return self._sse(path.rsplit("/", 1)[1])
        name = os.path.basename(path)
        file = os.path.join(ASSETS, name)
        if os.path.isfile(file) and os.path.splitext(name)[1] in TYPES:
            with open(file, "rb") as f:
                return self._send(200, f.read(), TYPES[os.path.splitext(name)[1]])
        return self._send(404, {"error": "not found"})

    def _sse(self, job_id):
        job = bus.JOBS.get(job_id)
        if job is None:
            return self._send(404, {"error": "unknown job"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for ev in job.stream():
                chunk = ": keep-alive\n\n" if ev is None else f"data: {json.dumps(ev, default=str)}\n\n"
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})
        path = self.path.split("?")[0]
        if path.startswith("/api/dag/"):
            from ..dagscope.routes import handle_post
            result = handle_post(path, body, bus)
            if result is not None:
                return self._send(*result)
        if path == "/api/replay":
            job = bus.start("replay", stream.job_replay, body["task"], int(body.get("run", 1)), float(body.get("speed", 1.0)))
        elif path == "/api/investigate":
            job = bus.start("investigate", stream.job_investigate, body["task"])
        elif path == "/api/verify":
            job = bus.start("verify", stream.job_verify, body["task"], int(body.get("run", 1)))
        elif path == "/api/bench":
            job = bus.start("bench", stream.job_bench)
        elif path == "/api/draft":
            job = bus.start("draft", stream.job_draft, str(body.get("description", ""))[:2000])
        else:
            return self._send(404, {"error": "not found"})
        return self._send(200, {"job": job.id})


def serve(host="127.0.0.1", port=8080):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"RootProof Live on http://{host}:{port}/", flush=True)
    srv.serve_forever()
