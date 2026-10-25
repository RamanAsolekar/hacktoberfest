"""Run the 5-minute demo end to end from the terminal (needs api, worker and watcher running).

  python -m scripts.demo                # clean slate, stack A (fresh), stack B (from memory), metrics
  python -m scripts.demo --no-approve   # stop at each approval so a human approves in the UI or Slack

Stack A: Python app, DB_PASSWORD missing  -> "fe_sendauth: no password supplied"
Stack B: Node app, PGPASSWORD missing     -> "SASL: ... client password must be a string" (same cause, different text)
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"resolved", "rolled_back", "rejected", "expired"}


def _bash() -> str:
    """Git Bash on Windows (plain `bash` there is WSL's, which can't see Docker Desktop the same way)."""
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash") or "bash"


def sh(script: str) -> None:
    proc = subprocess.run([_bash(), str(ROOT / "demo" / script)], capture_output=True, text=True)
    if proc.returncode != 0 and not script.endswith("down.sh"):
        sys.exit(f"{script} failed: {proc.stderr[-500:]}")


def newest(project: str, after: set[str]) -> dict | None:
    for inc in httpx.get(f"{API}/api/incidents", timeout=10).json():
        if inc["id"] not in after and (inc.get("context") or {}).get("compose_project") == project:
            return inc
    return None


def wait_for(incident_id: str, statuses: set[str], timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        inc = httpx.get(f"{API}/api/incidents/{incident_id}", timeout=10).json()
        if inc["status"] in statuses:
            return inc
        time.sleep(3)
    sys.exit(f"timed out waiting for {statuses}")


def show(inc: dict) -> None:
    m = inc.get("match") or {}
    print(f"   error      : {inc['symptom'].get('error_message', '')[-95:]}")
    if inc.get("source") == "memory":
        past = (m.get("matched_incident") or {}).get("symptom", {}).get("error_message", "")
        print(f"   FROM MEMORY: {round(m.get('confidence', 0) * 100)}% confident, matched past error: ...{past[-60:]}")
        print(f"   why        : {m.get('explanation')}")
    else:
        print(f"   FRESH      : no trusted memory match (best was {round(((m.get('memory_check') or {}).get('confidence') or 0) * 100)}%)")
    print(f"   root cause : {inc.get('root_cause')}")
    print(f"   fix        : {inc['fix'].get('type')}  {inc['fix'].get('content')}")


def run_case(label: str, stack: str, auto_approve: bool) -> dict:
    seen = {i["id"] for i in httpx.get(f"{API}/api/incidents", timeout=10).json()}
    print(f"\n== {label}: breaking {stack}")
    t0 = time.time()
    sh(f"{stack}/break.sh")
    inc = None
    while inc is None:
        time.sleep(2)
        inc = newest(stack, seen)
    print(f"   detected after {time.time() - t0:.0f}s, incident {inc['id']}")
    inc = wait_for(inc["id"], {"awaiting_approval"} | TERMINAL)
    print(f"   proposal ready after {time.time() - t0:.0f}s")
    show(inc)
    if inc["status"] != "awaiting_approval":
        return inc
    if auto_approve:
        httpx.post(f"{API}/api/incidents/{inc['id']}/approve", json={"approved_by": "demo"}, timeout=30)
        print("   approved (demo)")
    else:
        print("   waiting for a human to approve in the UI / Slack ...")
    inc = wait_for(inc["id"], TERMINAL)
    print(f"   RESULT     : {inc['status']} after {time.time() - t0:.0f}s total")
    return inc


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-approve", action="store_true")
    a = p.parse_args()
    print("resetting: demo stacks down, demo incidents cleared (seed memory kept)")
    for s in ("stack-a", "stack-b"):
        sh(f"{s}/down.sh")
    httpx.post(f"{API}/api/demo/reset", timeout=60)
    run_case("STEP 1 (first time)", "stack-a", not a.no_approve)
    run_case("STEP 2 (same cause, different error text)", "stack-b", not a.no_approve)
    m = httpx.get(f"{API}/api/metrics", timeout=30).json()
    print("\n== METRICS")
    print(f"   first-time avg time to resolve : {m['first_time_avg_ttr_sec']} s")
    print(f"   repeat (memory) avg            : {m['repeat_avg_ttr_sec']} s")
    print(f"   resolved by memory             : {m['pct_resolved_by_memory']}")
    print(f"   suggestion accuracy            : {m['suggestion_accuracy']}")
    print(f"   recurred after fix             : {m['recurred_count']}")
    print(f"   lessons in memory              : {m['memory_total']}")


if __name__ == "__main__":
    main()
