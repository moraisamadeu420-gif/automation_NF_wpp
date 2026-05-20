"""
app/workers/nfse_worker.py
Placeholder for a future async queue worker (Celery / ARQ / asyncio.Queue).
Currently emissions are triggered inline from the message processor.
This module is here so the architecture supports decoupling when traffic grows.
"""
from loguru import logger


async def process_emission_task(task_data: dict) -> None:
    """Entry point for queue-based emission tasks."""
    logger.info("Worker received task: {}", task_data)
    # TODO: dequeue and call NfseService.emit() when queue integration is added
