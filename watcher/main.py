"""Watcher: turns a crashing container into POST /api/failures within seconds.

Run: python -m watcher.main   (needs access to the Docker daemon)

Triggers: `die` with a non-zero exit code, `oom`, and `health_status: unhealthy`.
Skipped: exit 0, containers labelled dockergpt.ignore=true, ignored compose projects, and
containers we just stopped or killed ourselves (a recreate during a fix is not a failure).
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque

import docker
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
IGNORE_PROJECTS = {p.strip() for p in os.environ.get("IGNORE_PROJECTS", "nudgebee").split(",") if p.strip()}
WATCH_PROJECTS = {p.strip() for p in os.environ.get("WATCH_PROJECTS", "").split(",") if p.strip()}
DEBOUNCE_SEC = int(os.environ.get("WATCH_DEBOUNCE_SEC", "120"))
SETTLE_SEC = float(os.environ.get("WATCH_SETTLE_SEC", "6"))

log = logging.getLogger("watcher")

_last_report: dict[str, float] = {}
_deaths: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
_stopped_by_request: dict[str, float] = {}


def _labels(attrs: dict) -> dict:
    return (attrs.get("Config") or {}).get("Labels") or {}


def _service_key(attrs: dict) -> str:
    lb = _labels(attrs)
    return f"{lb.get('com.docker.compose.project', '-')}/{lb.get('com.docker.compose.service', attrs.get('Name', '?'))}"


def _error_line(lines: list[str]) -> str:
    keywords = ("error", "fatal", "exception", "failed", "denied", "refused", "killed", "panic", "cannot", "unable")
    for line in reversed(lines):
        if any(k in line.lower() for k in keywords):
            return line.strip()[:300]
    return (lines[-1].strip() if lines else "container exited")[:300]


def _mem(value: int | None) -> str | None:
    if not value:
        return None
    return f"{value // (1024 * 1024)}m"


def build_payload(container, reason: str) -> dict:
    attrs = container.attrs
    cfg = attrs.get("Config") or {}
    state = attrs.get("State") or {}
    host = attrs.get("HostConfig") or {}
    lb = _labels(attrs)
    try:
        raw = container.logs(tail=50, timestamps=False).decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    image = cfg.get("Image") or ""
    name, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
    key = _service_key(attrs)
    now = time.time()
    recent = [t for t in _deaths[key] if now - t < 300]
    stats = {}
    try:
        s = container.stats(stream=False)
        mem = (s.get("memory_stats") or {}).get("usage")
        stats["mem_mb"] = round(mem / (1024 * 1024), 1) if mem else None
        cpu = s.get("cpu_stats") or {}
        pre = s.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
        sys_delta = (cpu.get("system_cpu_usage") or 0) - (pre.get("system_cpu_usage") or 0)
        if cpu_delta > 0 and sys_delta > 0:
            stats["cpu_pct"] = round(cpu_delta / sys_delta * (cpu.get("online_cpus") or 1) * 100, 2)
    except Exception:
        pass
    config_files = [f for f in (lb.get("com.docker.compose.project.config_files") or "").split(",") if f]
    return {
        "symptom": {
            "error_message": _error_line(lines) if reason != "oom" else "container killed: OOMKilled (exit 137)",
            "exit_code": state.get("ExitCode"),
            "restart_count": attrs.get("RestartCount", 0),
            "restart_pattern": f"{reason}; {len(recent)} exits in the last 5 min",
            "oom_killed": bool(state.get("OOMKilled")),
        },
        "context": {
            "container": attrs.get("Name", "").lstrip("/"),
            "image": name or image,
            "image_tag": tag or "latest",
            "base_image": lb.get("org.opencontainers.image.base.name"),
            "compose_project": lb.get("com.docker.compose.project"),
            "compose_service": lb.get("com.docker.compose.service"),
            "compose_dir": lb.get("com.docker.compose.project.working_dir"),
            "compose_files": config_files,
            "mem_limit": _mem(host.get("Memory")),
            "cpu_limit": (host.get("NanoCpus") or 0) / 1e9 or None,
            "restart_policy": (host.get("RestartPolicy") or {}).get("Name"),
            "env_keys": sorted({e.split("=", 1)[0] for e in (cfg.get("Env") or [])}),
        },
        "evidence": {"log_lines": lines[-50:], "resource_stats": stats, "trigger": reason},
    }


def report(container_id: str, reason: str) -> None:
    """Wait a moment so logs are flushed, then post (in a thread so the event stream keeps flowing)."""
    time.sleep(SETTLE_SEC)
    client = docker.from_env()
    try:
        c = client.containers.get(container_id)
    except docker.errors.NotFound:
        return
    payload = build_payload(c, reason)
    for attempt in range(3):
        try:
            r = httpx.post(f"{API_URL}/api/failures", json=payload, timeout=30)
            r.raise_for_status()
            body = r.json()
            log.info("reported %s/%s -> incident %s%s", payload["context"]["compose_project"],
                     payload["context"]["compose_service"], body.get("incident_id"),
                     " (dedup)" if body.get("deduplicated") else "")
            return
        except Exception as exc:
            log.warning("report attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2)


def should_watch(attrs: dict) -> bool:
    lb = _labels(attrs)
    if lb.get("dockergpt.ignore") == "true":
        return False
    project = lb.get("com.docker.compose.project")
    if project in IGNORE_PROJECTS:
        return False
    if WATCH_PROJECTS and project not in WATCH_PROJECTS:
        return False
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = docker.from_env()
    log.info("watching Docker events; reporting to %s (ignoring projects: %s)", API_URL, ", ".join(IGNORE_PROJECTS) or "none")
    for ev in client.events(decode=True, filters={"type": "container"}):
        action = (ev.get("Action") or ev.get("status") or "").split(":")[0]
        cid = ev.get("id") or (ev.get("Actor") or {}).get("ID")
        if not cid:
            continue
        if action in ("kill", "stop"):  # intentional stop/recreate, e.g. while a fix is applied
            _stopped_by_request[cid] = time.time()
            continue
        full = ev.get("Action") or ""
        if action == "die":
            attrs_ev = (ev.get("Actor") or {}).get("Attributes") or {}
            exit_code = int(attrs_ev.get("exitCode", "0") or 0)
            if exit_code == 0 or time.time() - _stopped_by_request.get(cid, 0) < 15:
                continue
            reason = "crashed"
        elif action == "oom":
            reason = "oom"
        elif full.startswith("health_status") and "unhealthy" in full:
            reason = "unhealthy"
        else:
            continue
        try:
            container = client.containers.get(cid)
        except docker.errors.NotFound:
            continue
        if not should_watch(container.attrs):
            continue
        key = _service_key(container.attrs)
        _deaths[key].append(time.time())
        if time.time() - _last_report.get(key, 0) < DEBOUNCE_SEC:
            continue
        _last_report[key] = time.time()
        log.info("failure detected: %s (%s)", key, reason)
        threading.Thread(target=report, args=(cid, reason), daemon=True).start()


if __name__ == "__main__":
    main()
