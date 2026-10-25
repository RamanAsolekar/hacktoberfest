"""Manage the JarvisLabs GPU instances that serve DockerGPT Memory's models.

Usage (from dockergpt-memory/, with JL_API_KEY in .env):
  python -m scripts.jarvis up            # create (or resume) the L4 instance: Qwen2.5-7B chat + bge-small embeddings
  python -m scripts.jarvis up --large    # create (or resume) an A100 instance with Qwen2.5-14B, for the benchmark
  python -m scripts.jarvis status        # list instances, cost so far, and whether the APIs answer
  python -m scripts.jarvis wait          # wait until the model APIs answer
  python -m scripts.jarvis pause [--large]   # stop GPU billing (keeps the disk)
  python -m scripts.jarvis destroy [--large] # delete the instance and its disk

It writes LLM_BASE_URL / EMBED_BASE_URL / LLM_API_KEY (or LLM_LARGE_*) into .env, and keeps
instance ids in jarvis/instance.json (git-ignored).
"""

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
STATE = ROOT / "jarvis" / "instance.json"
STARTUP = ROOT / "jarvis" / "startup.sh"

PROFILES = {
    "small": {"gpu": "L4", "name": "dockergpt-llm", "chat": "Qwen/Qwen2.5-7B-Instruct",
              "embed": "BAAI/bge-small-en-v1.5", "util": "0.80", "mode": "full", "ports": "8000,8001", "storage": 60},
    "large": {"gpu": "A100", "name": "dockergpt-llm-large", "chat": "Qwen/Qwen2.5-14B-Instruct",
              "embed": "", "util": "0.90", "mode": "chat-only", "ports": "8000", "storage": 80},
}


def _env() -> dict:
    return {k: v for k, v in dotenv_values(ENV).items() if v is not None} if ENV.exists() else {}


def _client():
    import os

    from jarvislabs import Client

    key = os.environ.get("JL_API_KEY") or _env().get("JL_API_KEY")
    if not key:
        sys.exit("JL_API_KEY is empty. Add your JarvisLabs API key to dockergpt-memory/.env first.")
    return Client(api_key=key)


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def _script_id(client, name: str) -> int:
    content = STARTUP.read_text(encoding="utf-8").replace("\r\n", "\n")
    existing = [s for s in client.scripts.list() if getattr(s, "script_name", None) == name or getattr(s, "name", None) == name]
    if existing:
        sid = int(getattr(existing[0], "script_id", None) or getattr(existing[0], "id"))
        client.scripts.update(sid, content)
        return sid
    client.scripts.add(content, name=name)
    for s in client.scripts.list():
        if getattr(s, "script_name", None) == name or getattr(s, "name", None) == name:
            return int(getattr(s, "script_id", None) or getattr(s, "id"))
    raise RuntimeError("uploaded the startup script but could not find its id")


def _endpoint_for(inst, port: str) -> str | None:
    eps = inst.endpoints or []
    for ep in eps:
        if f"-{port}." in ep or f":{port}" in ep or ep.rstrip("/").endswith(port):
            return ep.rstrip("/")
    ports = [p.strip() for p in (inst.http_ports or "").split(",") if p.strip()]
    if port in ports and len(eps) == len(ports):
        return eps[ports.index(port)].rstrip("/")
    return None


def up(large: bool) -> None:
    profile = PROFILES["large" if large else "small"]
    client = _client()
    state = _state()
    env = _env()
    api_key = env.get("LLM_LARGE_API_KEY" if large else "LLM_API_KEY") or secrets.token_urlsafe(24)
    sid = _script_id(client, "dockergpt-startup")
    args = f"{api_key} {profile['chat']} {profile['embed'] or 'none'} {profile['util']} {profile['mode']}"
    key = "large" if large else "small"
    machine_id = state.get(key)
    inst = None
    if machine_id:
        try:
            inst = client.instances.get(machine_id)
        except Exception:
            inst = None
    if inst and inst.status == "Paused":
        print(f"resuming instance {machine_id} ...")
        inst = client.instances.resume(machine_id, http_ports=profile["ports"], script_id=str(sid), script_args=args)
    elif inst and inst.status == "Running":
        print(f"instance {machine_id} already running")
    else:
        print(f"creating {profile['gpu']} instance '{profile['name']}' (this waits until it is running) ...")
        inst = client.instances.create(
            gpu_type=profile["gpu"], template="pytorch", storage=profile["storage"], name=profile["name"],
            http_ports=profile["ports"], script_id=str(sid), script_args=args,
        )
    state[key] = inst.machine_id
    _save_state(state)
    inst = client.instances.get(inst.machine_id)
    print(f"machine {inst.machine_id}: {inst.status}, gpu {inst.gpu_type}, endpoints {inst.endpoints}")
    chat = _endpoint_for(inst, "8000")
    if not chat:
        sys.exit(f"could not work out the port-8000 URL from endpoints {inst.endpoints}; set LLM_BASE_URL by hand")
    if large:
        set_key(str(ENV), "LLM_LARGE_BASE_URL", chat + "/v1", quote_mode="never")
        set_key(str(ENV), "LLM_LARGE_API_KEY", api_key, quote_mode="never")
        set_key(str(ENV), "LLM_LARGE_MODEL", profile["chat"], quote_mode="never")
    else:
        emb = _endpoint_for(inst, "8001")
        set_key(str(ENV), "LLM_BASE_URL", chat + "/v1", quote_mode="never")
        set_key(str(ENV), "LLM_API_KEY", api_key, quote_mode="never")
        set_key(str(ENV), "LLM_MODEL", profile["chat"], quote_mode="never")
        if emb:
            set_key(str(ENV), "EMBED_BASE_URL", emb + "/v1", quote_mode="never")
            set_key(str(ENV), "EMBED_API_KEY", api_key, quote_mode="never")
            set_key(str(ENV), "EMBED_MODEL", profile["embed"], quote_mode="never")
    print("wrote model URLs to .env. The models download on first boot (5-15 min); run `python -m scripts.jarvis wait`.")


def _probe(url: str, key: str) -> str:
    if not url:
        return "not configured"
    try:
        r = httpx.get(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200:
            return "ready: " + ", ".join(m["id"] for m in r.json().get("data", []))
        return f"HTTP {r.status_code}"
    except Exception as exc:
        return f"not answering ({type(exc).__name__})"


def status() -> None:
    client = _client()
    for inst in client.instances.list():
        print(f"- {inst.machine_id} {inst.name}: {inst.status}, {inst.gpu_type}, cost so far {inst.cost}, endpoints {inst.endpoints}")
    env = _env()
    print("chat :", _probe(env.get("LLM_BASE_URL", ""), env.get("LLM_API_KEY", "")))
    print("embed:", _probe(env.get("EMBED_BASE_URL", ""), env.get("EMBED_API_KEY", "")))
    if env.get("LLM_LARGE_BASE_URL"):
        print("large:", _probe(env.get("LLM_LARGE_BASE_URL", ""), env.get("LLM_LARGE_API_KEY", "")))


def wait(timeout_min: int = 30, large: bool = False) -> None:
    env = _env()
    targets = ([("large", env.get("LLM_LARGE_BASE_URL", ""), env.get("LLM_LARGE_API_KEY", ""))] if large else
               [("chat", env.get("LLM_BASE_URL", ""), env.get("LLM_API_KEY", "")),
                ("embed", env.get("EMBED_BASE_URL", ""), env.get("EMBED_API_KEY", ""))])
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        results = {n: _probe(u, k) for n, u, k in targets}
        print(time.strftime("%H:%M:%S"), results, flush=True)
        if all(r.startswith("ready") for r in results.values()):
            return
        time.sleep(20)
    sys.exit("models did not come up in time; check /home/dockergpt/*.log on the instance")


def lifecycle(action: str, large: bool) -> None:
    client = _client()
    key = "large" if large else "small"
    mid = _state().get(key)
    if not mid:
        sys.exit(f"no {key} instance recorded in {STATE}")
    if action == "pause":
        client.instances.pause(mid)
        print(f"paused {mid}; GPU billing stopped")
    else:
        client.instances.destroy(mid)
        state = _state()
        state.pop(key, None)
        _save_state(state)
        print(f"destroyed {mid}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["up", "status", "wait", "pause", "destroy"])
    p.add_argument("--large", action="store_true")
    a = p.parse_args()
    if a.action == "up":
        up(a.large)
    elif a.action == "status":
        status()
    elif a.action == "wait":
        wait(large=a.large)
    else:
        lifecycle(a.action, a.large)


if __name__ == "__main__":
    main()
