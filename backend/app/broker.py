from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.config import settings

# Results and progress records share this one-hour retention window.
result_backend = RedisAsyncResultBackend(
    redis_url=settings.redis_url,
    result_ex_time=3600,
    prefix_str="tasks:result",
)

# Redis Streams are intentionally separated by workload.  Document ingestion
# must never compete with agent jobs for a consumer slot.
documents_broker = RedisStreamBroker(
    url=settings.redis_url,
    queue_name=settings.documents_queue_name,
)

tasks_broker = RedisStreamBroker(
    url=settings.redis_url,
    queue_name=settings.tasks_queue_name,
).with_result_backend(result_backend)
