"""DockerGPT Memory API: `uvicorn api.main:app --port 8000` from the dockergpt-memory folder."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.agent.routes import router as agent_router
from api.store.db import init_db
from api.store.routes_incidents import router as incidents_router
from api.store.routes_metrics import router as metrics_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="DockerGPT Memory", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(agent_router)
app.include_router(incidents_router)
app.include_router(metrics_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
