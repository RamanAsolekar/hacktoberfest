#!/bin/bash
# JarvisLabs startup script for DockerGPT Memory.
# Runs on instance launch/resume. Serves two OpenAI-compatible APIs:
#   :8000  chat model via vLLM          (/v1/chat/completions)
#   :8001  embedding model via a small FastAPI server (/v1/embeddings)
# Args: <api_key> <chat_model> <embed_model> <gpu_mem_fraction> <mode: full|chat-only>
API_KEY="$1"
CHAT_MODEL="${2:-Qwen/Qwen2.5-7B-Instruct}"
EMBED_MODEL="${3:-BAAI/bge-small-en-v1.5}"
GPU_UTIL="${4:-0.80}"
MODE="${5:-full}"

WORK=/home/dockergpt
mkdir -p "$WORK"
exec >>"$WORK/startup.log" 2>&1
echo "=== startup $(date -u) chat=$CHAT_MODEL embed=$EMBED_MODEL mode=$MODE"

if ! python -c "import vllm" 2>/dev/null; then
  pip install -q --upgrade pip
  pip install -q "vllm>=0.6.4"
fi
python -c "import sentence_transformers" 2>/dev/null || pip install -q "sentence-transformers>=3.2" fastapi uvicorn

pkill -f "vllm.entrypoints.openai.api_server" || true
pkill -f "dockergpt_embed_server.py" || true

nohup python -m vllm.entrypoints.openai.api_server \
  --model "$CHAT_MODEL" --served-model-name "$CHAT_MODEL" \
  --host 0.0.0.0 --port 8000 --api-key "$API_KEY" \
  --gpu-memory-utilization "$GPU_UTIL" --max-model-len 8192 \
  >"$WORK/vllm.log" 2>&1 &
echo "vllm started pid $!"

if [ "$MODE" = "full" ]; then
cat >"$WORK/dockergpt_embed_server.py" <<'PY'
import os, sys
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import uvicorn

API_KEY, MODEL = sys.argv[1], sys.argv[2]
model = SentenceTransformer(MODEL, device="cuda")
app = FastAPI()

def check(auth):
    if auth != f"Bearer {API_KEY}":
        raise HTTPException(401, "bad api key")

class EmbIn(BaseModel):
    input: list[str] | str
    model: str | None = None

@app.get("/v1/models")
def models(authorization: str | None = Header(None)):
    check(authorization)
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}

@app.post("/v1/embeddings")
def embeddings(body: EmbIn, authorization: str | None = Header(None)):
    check(authorization)
    texts = [body.input] if isinstance(body.input, str) else body.input
    vecs = model.encode(texts, normalize_embeddings=True).tolist()
    return {"object": "list", "model": MODEL,
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
            "usage": {"prompt_tokens": 0, "total_tokens": 0}}

@app.get("/health")
def health():
    return {"ok": True}

uvicorn.run(app, host="0.0.0.0", port=8001)
PY
nohup python "$WORK/dockergpt_embed_server.py" "$API_KEY" "$EMBED_MODEL" >"$WORK/embed.log" 2>&1 &
echo "embed server started pid $!"
fi
echo "=== startup script done $(date -u)"
