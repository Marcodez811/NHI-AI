from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.config import settings

# Results and progress records share this one-hour retention window.
result_backend = RedisAsyncResultBackend(
    redis_url=settings.redis_url,
    result_ex_time=3600,
    prefix_str="slides:result",
)

# Redis Streams is the queue; result_backend stores terminal results and progress.
broker = RedisStreamBroker(
    url=settings.redis_url,
    queue_name="slides",
).with_result_backend(result_backend)
