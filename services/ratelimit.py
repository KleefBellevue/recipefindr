"""Small in-memory rate limiter so a public deployment can't burn through the free API quotas.

Best effort: on serverless hosts each warm instance keeps its own counts.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    # Vercel sets x-real-ip / x-forwarded-for to the real client address.
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        key = client_ip(request)
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 5000:  # keep memory bounded
                self._hits.clear()
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(429, "You're going a bit fast! Please wait a few minutes and try again.")
            hits.append(now)


# Per visitor, per 10 minutes.
detect_limiter = RateLimiter(limit=15, window_seconds=600)
edit_limiter = RateLimiter(limit=30, window_seconds=600)
recipe_limiter = RateLimiter(limit=20, window_seconds=600)
