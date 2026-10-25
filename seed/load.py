"""Load seed/library.jsonl into memory (Postgres + Qdrant). Safe to run twice.

Loader by Person 1 as a stand-in; Person 4 owns seed/ and expands the library to 18 cases.
Run: python -m seed.load
"""

import json
from pathlib import Path

from api.agent.guard import guard
from api.agent.retrieval import index_incident
from api.store import repo
from api.store.db import init_db
from api.store.scrubber import scrub

LIBRARY = Path(__file__).with_name("library.jsonl")


def load_library(path: Path = LIBRARY) -> int:
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        external_id = case.pop("id")
        if repo.get_by_external_id(external_id):
            continue
        record = guard(scrub({**case, "external_id": external_id, "source": "seed", "status": "resolved"}))
        record.setdefault("fix", {})["held"] = True
        stored = repo.create_incident(record)
        index_incident(stored)
        loaded += 1
    return loaded


if __name__ == "__main__":
    init_db()
    print({"loaded": load_library()})
