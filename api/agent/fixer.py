"""Apply an approved fix to a compose stack, verify health, and roll back on failure.

Safety rules: only files under DEMO_ROOT are touched, a backup is written first, commands come from
an allowlist, and nothing from the LLM is ever run through a shell.
"""

import io
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import docker
from ruamel.yaml import YAML

from api.config import settings
from api.store.scrubber import scrub_text

COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
_CMD_ALLOW = re.compile(r"^docker compose (up -d|restart) ([A-Za-z0-9_.-]+)$")
_PATCH_LINE = re.compile(r"^services\.([A-Za-z0-9_.-]+?)\.([A-Za-z0-9_.\-]+):\s*(.+)$")
_ENV_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


class FixRefused(Exception):
    """The fix is unsafe or malformed; nothing was changed."""


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _under_root(path: Path) -> Path:
    root = Path(settings.demo_root).resolve()
    p = path.resolve()
    if os.path.normcase(str(p)) != os.path.normcase(str(root)) and not os.path.normcase(str(p)).startswith(
        os.path.normcase(str(root)) + os.sep
    ):
        raise FixRefused(f"{p} is outside DEMO_ROOT ({root}); refusing to touch it")
    return p


def compose_file_for(context: dict[str, Any]) -> Path:
    files = context.get("compose_files") or []
    if files:
        return _under_root(Path(files[0]))
    d = context.get("compose_dir")
    if not d:
        raise FixRefused("incident has no compose_dir")
    base = _under_root(Path(d))
    for name in COMPOSE_NAMES:
        if (base / name).exists():
            return base / name
    raise FixRefused(f"no compose file in {base}")


def read_stack(context: dict[str, Any]) -> dict[str, Any] | None:
    """Compose text (scrubbed) and .env key names, for the LLM to adapt fixes to this stack."""
    try:
        path = compose_file_for(context)
    except FixRefused:
        return None
    env_keys: list[str] = []
    env_path = path.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            m = _ENV_LINE.match(line.strip())
            if m:
                env_keys.append(m.group(1))
    return {"compose_text": scrub_text(path.read_text(encoding="utf-8")), "env_file_keys": env_keys,
            "compose_file": str(path)}


def _set_path(node: Any, keys: list[str], value: Any) -> None:
    for i, key in enumerate(keys):
        last = i == len(keys) - 1
        if isinstance(node, list):
            raise FixRefused("cannot address into a list here")
        child = node.get(key)
        if last:
            if isinstance(child, list) and key == "environment":
                raise FixRefused("environment needs a variable name")
            node[key] = value
            return
        nxt = keys[i + 1]
        if child is None:
            child = {}
            node[key] = child
        elif isinstance(child, list):
            if key == "environment":  # list form: ["A=b", ...]
                if i + 2 != len(keys):
                    raise FixRefused("bad environment path")
                entries = [e for e in child if not str(e).split("=", 1)[0] == nxt]
                entries.append(f"{nxt}={value}")
                child.clear()
                child.extend(entries)
                return
            if key == "depends_on":  # short form ["db"] -> long form
                as_map = {str(name): {"condition": "service_started"} for name in child}
                node[key] = as_map
                child = as_map
            else:
                raise FixRefused(f"cannot patch list field {key}")
        node = child


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("${") or not raw:
        return raw.strip("'\"")
    try:
        return YAML(typ="safe").load(raw)
    except Exception:
        return raw


def _compose(args: list[str], cwd: Path, timeout: int = 180) -> str:
    proc = subprocess.run(["docker", "compose", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {proc.stderr.strip()[-600:]}")
    return proc.stdout + proc.stderr


def apply_fix(context: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    """Back up, patch, then `docker compose up -d <service>`. Returns what rollback needs."""
    ftype = fix.get("type")
    content = (fix.get("content") or "").strip()
    project = context.get("compose_project")
    service = context.get("compose_service")
    path = compose_file_for(context)
    stack_dir = path.parent
    touched: list[str] = []

    if ftype == "command":
        m = _CMD_ALLOW.match(content)
        if not m:
            raise FixRefused(f"command not on the allowlist: {content!r}")
        service = m.group(2)
    elif ftype == "compose_patch":
        y = _yaml()
        doc = y.load(path.read_text(encoding="utf-8"))
        lines = [ln.strip().strip("`") for ln in content.splitlines() if ln.strip()]
        if not lines:
            raise FixRefused("empty compose_patch")
        for line in lines:
            m = _PATCH_LINE.match(line)
            if not m:
                raise FixRefused(f"bad compose_patch line: {line!r}")
            svc, dotted, raw = m.groups()
            if svc not in (doc.get("services") or {}):
                raise FixRefused(f"service {svc!r} is not in {path.name}")
            _set_path(doc["services"][svc], dotted.split("."), _parse_scalar(raw))
        shutil.copy2(path, str(path) + ".dockergpt.bak")
        buf = io.StringIO()
        y.dump(doc, buf)
        path.write_text(buf.getvalue(), encoding="utf-8")
        touched.append(str(path))
    elif ftype == "env_patch":
        env_path = stack_dir / ".env"
        existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        updates: dict[str, str] = {}
        for line in content.splitlines():
            m = _ENV_LINE.match(line.strip())
            if not m:
                raise FixRefused(f"bad env_patch line: {line!r}")
            updates[m.group(1)] = m.group(2)
        if env_path.exists():
            shutil.copy2(env_path, str(env_path) + ".dockergpt.bak")
        kept = [ln for ln in existing if not (_ENV_LINE.match(ln.strip()) and ln.split("=", 1)[0] in updates)]
        env_path.write_text("\n".join(kept + [f"{k}={v}" for k, v in updates.items()]) + "\n", encoding="utf-8")
        touched.append(str(env_path))
    else:
        raise FixRefused(f"unknown fix type {ftype!r}")

    args = ["-f", str(path)] + (["-p", project] if project else [])
    if ftype == "command" and content.startswith("docker compose restart"):
        _compose(args + ["restart", service], stack_dir)
    else:
        _compose(args + ["up", "-d", service], stack_dir)
    return {"touched": touched, "compose_file": str(path), "project": project, "service": service}


def rollback(applied: dict[str, Any]) -> None:
    for f in applied.get("touched") or []:
        bak = f + ".dockergpt.bak"
        if os.path.exists(bak):
            shutil.copy2(bak, f)
    path = Path(applied["compose_file"])
    args = ["-f", str(path)] + (["-p", applied["project"]] if applied.get("project") else [])
    _compose(args + ["up", "-d", applied["service"]], path.parent)


def _containers(project: str | None, service: str) -> list:
    labels = [f"com.docker.compose.service={service}"]
    if project:
        labels.append(f"com.docker.compose.project={project}")
    return docker.from_env().containers.list(all=True, filters={"label": labels})


def verify_health(project: str | None, service: str, window_sec: int | None = None) -> dict[str, Any]:
    """Healthy = running for the whole window, restart count not rising, health check not unhealthy."""
    window = window_sec or settings.health_window_sec
    time.sleep(3)
    first = {c.id: c.attrs.get("RestartCount", 0) for c in _containers(project, service)}
    if not first:
        return {"healthy": False, "detail": "no container found for the service"}
    deadline = time.time() + window
    while time.time() < deadline:
        for c in _containers(project, service):
            c.reload()
            state = c.attrs.get("State", {})
            if state.get("Status") != "running":
                return {"healthy": False, "detail": f"container {c.name} is {state.get('Status')} (exit {state.get('ExitCode')})"}
            if c.attrs.get("RestartCount", 0) > first.get(c.id, 0):
                return {"healthy": False, "detail": f"container {c.name} restarted during the check"}
            if (state.get("Health") or {}).get("Status") == "unhealthy":
                return {"healthy": False, "detail": f"container {c.name} health check is unhealthy"}
        time.sleep(2)
    return {"healthy": True, "detail": f"running with no restarts for {window}s"}
