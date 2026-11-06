"""Thin stdlib clients: OpenAI (subject AI) and TypeSafe Jev (typed extraction). Keys come from ../.env."""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBJECT_MODEL = "gpt-6-luna"
JEV_MODEL = "jev-latest"


def _env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _env()


def _post(url, key, body, timeout=300):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> {e.code}: {e.read().decode()[:500]}") from None


def ask_subject(prompt, model=SUBJECT_MODEL):
    """One fresh, stateless request = one fresh conversation. Returns (text, raw_response)."""
    raw = _post("https://api.openai.com/v1/responses", ENV["OPENAI"], {"model": model, "input": prompt})
    text = raw.get("output_text") or "".join(
        c.get("text", "") for o in raw.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
    return text, raw


JEV_CACHE = os.path.join(ROOT, "results", "jev_cache.json")
_cache = json.load(open(JEV_CACHE)) if os.path.exists(JEV_CACHE) else {}
_lock = threading.Lock()


def jev(state, questions, model=JEV_MODEL):
    """Cached by request hash so judges can re-run the verifier offline; the cache ships in results/."""
    body = {"model": model, "state": state, "questions": questions}
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    res = _post("https://api.typesafe.ai/v1/systemone", ENV["TYPESAFE"], body, timeout=60)
    with _lock:
        _cache[key] = res
        os.makedirs(os.path.dirname(JEV_CACHE), exist_ok=True)
        json.dump(_cache, open(JEV_CACHE, "w"))
    return res
