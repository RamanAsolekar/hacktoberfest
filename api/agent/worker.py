"""Temporal worker: `python -m api.agent.worker`.

Runs on the machine that owns the Docker stacks (it applies fixes with `docker compose`) and,
locally, next to Kepler (it runs `kepler exec`).
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from api.agent import activities as act
from api.agent.workflows import ResolveAndLearnWorkflow
from api.config import settings
from api.store.db import init_db


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    client = await Client.connect(settings.temporal_address)
    with ThreadPoolExecutor(max_workers=16) as pool:
        worker = Worker(
            client,
            task_queue=settings.task_queue,
            workflows=[ResolveAndLearnWorkflow],
            activities=[
                act.set_status, act.kepler_investigate, act.retrieve, act.reason, act.notify_approval,
                act.apply_fix, act.verify_health, act.rollback, act.learn,
            ],
            activity_executor=pool,
        )
        logging.info("worker listening on task queue %s at %s", settings.task_queue, settings.temporal_address)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
