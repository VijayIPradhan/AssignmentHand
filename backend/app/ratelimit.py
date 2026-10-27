"""Per-user sliding-window rate limiter for expensive endpoints.

Process-local; with several API replicas each replica enforces the limit independently. Swap for a
shared store (e.g. Redis) if you scale out horizontally.
"""

import threading
import time
from collections import defaultdict, deque

from app.errors import RateLimited

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def check_rate(user_id: str, bucket: str, per_minute: int) -> None:
    if per_minute <= 0:
        return
    now = time.monotonic()
    with _lock:
        q = _hits[(user_id, bucket)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            retry = int(60 - (now - q[0])) + 1
            raise RateLimited(f"Too many {bucket} requests. Try again in {retry}s.", details={"retry_after": retry})
        q.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
