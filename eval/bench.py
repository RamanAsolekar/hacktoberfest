"""Retrieval benchmark: how often memory finds the right past case, per embedding model.

Run from the repo root (host venv or `docker compose run --rm api python -m eval.bench`):
    python -m eval.bench                  # embedding retrieval: top-1, top-3, latency
    python -m eval.bench --judge          # + LLM judge confidence on the expected case

Inputs: seed/library.jsonl (the memory, each line an incident with `id`) and
seed/variants.jsonl (the same failures reworded, each with `expected_id`; same shape as an
incident: symptom / context / evidence). Each embedding model gets its own temporary Qdrant
collection, deleted afterwards. Results go to eval/results.md.
"""

import argparse
import json
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from api.agent import llm
from api.agent.retrieval import build_embedding_text
from api.config import PROJECT_ROOT, settings

LIBRARY = PROJECT_ROOT / "seed" / "library.jsonl"
VARIANTS = PROJECT_ROOT / "seed" / "variants.jsonl"
RESULTS = PROJECT_ROOT / "eval" / "results.md"


def _env_list(name: str) -> list[str]:
    return [m.strip() for m in os.environ.get(name, "").split(",") if m.strip()]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def variant_text(variant: dict[str, Any]) -> str:
    """A new failure has no root cause yet, so leave it out (it would leak the answer)."""
    return build_embedding_text({k: v for k, v in variant.items() if k != "root_cause"})


def _embed_timed(texts: list[str], model: str) -> tuple[list[list[float]], float]:
    start = time.perf_counter()
    vecs = llm.embed(texts, model=model)
    return vecs, (time.perf_counter() - start) * 1000


def bench_embeddings(model: str, library: list[dict], variants: list[dict], qdrant: QdrantClient,
                     k: int = 3) -> dict[str, Any]:
    """Index the library, search each variant, record rank of expected_id and embed latency."""
    collection = f"bench_{uuid.uuid4().hex[:10]}"
    by_point = {str(uuid.uuid5(uuid.NAMESPACE_URL, inc["id"])): inc["id"] for inc in library}
    try:
        vecs, _ = _embed_timed([build_embedding_text(inc) for inc in library], model)
        qdrant.create_collection(collection, vectors_config=qm.VectorParams(size=len(vecs[0]), distance=qm.Distance.COSINE))
        qdrant.upsert(collection, points=[
            qm.PointStruct(id=pid, vector=v, payload={"incident_id": iid})
            for (pid, iid), v in zip(by_point.items(), vecs)
        ])
        top1 = top3 = 0
        latencies: list[float] = []
        per_case: list[dict[str, Any]] = []
        for var in variants:
            [vec], ms = _embed_timed([variant_text(var)], model)
            latencies.append(ms)
            hits = qdrant.query_points(collection, query=vec, limit=k, with_payload=True).points
            ranked = [h.payload["incident_id"] for h in hits]
            top1 += bool(ranked) and ranked[0] == var["expected_id"]
            top3 += var["expected_id"] in ranked
            per_case.append({"variant": var.get("id"), "expected": var["expected_id"], "ranked": ranked})
    finally:
        if qdrant.collection_exists(collection):
            qdrant.delete_collection(collection)
    n = len(variants)
    return {
        "model": model, "cases": n,
        "top1": 100 * top1 / n, "top3": 100 * top3 / n,
        "latency_ms": statistics.mean(latencies) if latencies else 0.0,
        "per_case": per_case,
    }


def _chat_endpoint(model: str) -> str | None:
    """The large benchmark model runs on its own JarvisLabs instance."""
    if model == os.environ.get("LLM_LARGE_MODEL") and os.environ.get("LLM_LARGE_BASE_URL"):
        return os.environ["LLM_LARGE_BASE_URL"]
    return None


def bench_judge(chat_model: str, embed_model: str, library: list[dict], variants: list[dict],
                retrieval: dict[str, Any]) -> dict[str, Any]:
    """Give judge_match the top-3 retrieved cases; count confident (>= threshold) picks of expected_id."""
    from api.agent.reasoning import judge_match

    by_id = {inc["id"]: inc for inc in library}
    confident = 0
    latencies: list[float] = []
    for var, case in zip(variants, retrieval["per_case"]):
        candidates = [{"incident": by_id[i], "score": None} for i in case["ranked"] if i in by_id]
        new = {k: v for k, v in var.items() if k not in ("root_cause", "expected_id")}
        start = time.perf_counter()
        try:
            result = judge_match(new, candidates, [], model=chat_model, base_url=_chat_endpoint(chat_model))
        except Exception as exc:
            print(f"  judge failed on {var.get('id')}: {exc}")
            continue
        finally:
            latencies.append((time.perf_counter() - start) * 1000)
        if (result.get("source") == "memory" and result.get("matched_incident_id") == var["expected_id"]
                and float(result.get("confidence") or 0) >= settings.confidence_threshold):
            confident += 1
    n = len(variants)
    return {"model": chat_model, "embed_model": embed_model, "cases": n,
            "confident_pct": 100 * confident / n,
            "latency_ms": statistics.mean(latencies) if latencies else 0.0}


def write_results(rows: list[dict], judge_rows: list[dict], path: Path) -> str:
    lines = [
        "# Retrieval benchmark",
        "",
        f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m eval.bench`. "
        "Each variant is a reworded failure; a hit means memory returned the case it came from.",
        "",
        "| Model | Cases | Top-1 % | Top-3 % | Avg latency ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines += [f"| {r['model']} | {r['cases']} | {r['top1']:.0f} | {r['top3']:.0f} | {r['latency_ms']:.0f} |" for r in rows]
    if judge_rows:
        lines += [
            "",
            f"## LLM judge (confidence >= {settings.confidence_threshold} on the expected case)",
            "",
            "| Chat model | Embedding model | Cases | Confident & correct % | Avg latency ms |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        lines += [f"| {j['model']} | {j['embed_model']} | {j['cases']} | {j['confident_pct']:.0f} | {j['latency_ms']:.0f} |"
                  for j in judge_rows]
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", type=Path, default=LIBRARY)
    ap.add_argument("--variants", type=Path, default=VARIANTS)
    ap.add_argument("--out", type=Path, default=RESULTS)
    ap.add_argument("--judge", action="store_true", help="also score api.agent.reasoning.judge_match per chat model")
    args = ap.parse_args()

    library, variants = load_jsonl(args.library), load_jsonl(args.variants)
    missing = {v["expected_id"] for v in variants} - {inc["id"] for inc in library}
    if missing:
        raise SystemExit(f"variants point at ids not in the library: {sorted(missing)}")

    embed_models = _env_list("BENCH_EMBED_MODELS") or [settings.embed_model]
    qdrant = QdrantClient(url=settings.qdrant_url, timeout=30)
    print(f"{len(library)} library cases, {len(variants)} variants, embedding models: {', '.join(embed_models)}")

    rows = []
    for model in embed_models:
        r = bench_embeddings(model, library, variants, qdrant)
        print(f"  {model}: top-1 {r['top1']:.0f}%  top-3 {r['top3']:.0f}%  {r['latency_ms']:.0f} ms")
        rows.append(r)

    judge_rows = []
    if args.judge:
        chat_models = _env_list("BENCH_LLM_MODELS") or [m for m in (settings.llm_model, os.environ.get("LLM_LARGE_MODEL")) if m]
        for chat in chat_models:
            j = bench_judge(chat, rows[0]["model"], library, variants, rows[0])
            print(f"  judge {chat}: confident & correct {j['confident_pct']:.0f}%  {j['latency_ms']:.0f} ms")
            judge_rows.append(j)

    print()
    print(write_results(rows, judge_rows, args.out))


if __name__ == "__main__":
    main()
