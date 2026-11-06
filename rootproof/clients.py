"""Clients for the two external models: the subject AI (OpenAI) and TypeSafe Jev (typed extraction).

Requests are stateless: one call is one fresh conversation. Jev responses are cached on disk, keyed by a
hash of the request body, so the verifier re-runs offline once the cache is shipped.
"""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request

from .config import JEV_CACHE, JEV_MODEL, ROOT, SUBJECT_MODEL


def _load_env(path=os.path.join(ROOT, ".env")):
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    # Compose injects env_file values into the container environment; it does not copy .env into the image.
    for name in ("OPENAI", "TYPESAFE"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _post(url, key, body, timeout=300):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> {e.code}: {e.read().decode()[:500]}") from None


class JsonCache:
    """Disk-backed map from request hash to response, safe to share between threads."""

    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path)) if os.path.exists(path) else {}
        self.lock = threading.Lock()

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self.data, f)


_env = _load_env()
_jev_cache = JsonCache(JEV_CACHE)


def ask_subject(prompt, model=SUBJECT_MODEL):
    """One fresh request = one fresh conversation. Returns (answer_text, raw_response)."""
    raw = _post("https://api.openai.com/v1/responses", _env["OPENAI"], {"model": model, "input": prompt})
    text = raw.get("output_text") or "".join(
        c.get("text", "") for o in raw.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    return text, raw


def jev(state, questions, model=JEV_MODEL):
    """Typed extraction. Served from the cache when the same request was made before."""
    body = {"model": model, "state": state, "questions": questions}
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    cached = _jev_cache.get(key)
    if cached is not None:
        return cached
    res = _post("https://api.typesafe.ai/v1/systemone", _env["TYPESAFE"], body, timeout=60)
    _jev_cache.put(key, res)
    return res
