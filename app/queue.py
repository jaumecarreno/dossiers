from __future__ import annotations

from flask import current_app
from redis import Redis
from rq import Queue


def get_redis_connection() -> Redis:
    return Redis.from_url(current_app.config["REDIS_URL"])


def get_queue() -> Queue:
    return Queue(
        current_app.config["RQ_QUEUE_NAME"],
        connection=get_redis_connection(),
        default_timeout=current_app.config["RQ_JOB_TIMEOUT_SECONDS"],
    )


def enqueue_project_processing(project_id: int):
    return get_queue().enqueue("app.tasks.process_project", project_id)
