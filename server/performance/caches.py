"""Cache Registry and Cache Policy Engine.

Every cache is registered with explicit purpose, bounds, TTL, privacy, and
invalidation triggers. Security-sensitive state (approvals, revoked
permissions/sessions, secrets, changed trust/policy) is refused by
construction. Revocation and deletion invalidate immediately rather than
relying on long TTLs. Clearing a cache through the registry only ever touches
caches flagged safe-to-clear.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import CacheRegistration, CacheStats
from .storage import PerformanceStorage

# Security-sensitive state that must never be cached.
NEVER_CACHE_PURPOSES = (
    "approval",
    "approval_result",
    "session_authority",
    "revoked_permission",
    "revoked_session",
    "secret",
    "secret_metadata",
    "permission_decision",
    "trust_state",
)


class Cache:
    def __init__(self, registration: CacheRegistration, *, now_provider=None) -> None:
        self.registration = registration
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._store: Dict[str, Any] = {}
        self._sizes: Dict[str, int] = {}
        self._expires: Dict[str, float] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._last_cleanup: Optional[str] = None

    def get(self, key: str) -> Tuple[Optional[Any], bool]:
        now = self._now()
        with self._lock:
            if key in self._expires and self._expires[key] < now:
                self._discard(key)
                self._misses += 1
                return None, False
            if key in self._store:
                self._hits += 1
                return self._store[key], True
            self._misses += 1
            return None, False

    def put(self, key: str, value: Any, size_bytes: int = 0) -> None:
        with self._lock:
            self._evict(1, size_bytes)
            if key not in self._store:
                self._store[key] = value
                self._sizes[key] = max(0, int(size_bytes))
                self._expires[key] = self._now() + self.registration.ttl_seconds
            else:
                self._store[key] = value
                self._sizes[key] = max(0, int(size_bytes))
                self._expires[key] = self._now() + self.registration.ttl_seconds

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._discard(key)

    def invalidate_all(self) -> None:
        with self._lock:
            self._store.clear()
            self._sizes.clear()
            self._expires.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def bytes_used(self) -> int:
        with self._lock:
            return sum(self._sizes.values())

    def stats(self) -> CacheStats:
        with self._lock:
            self._expire_stale()
            return CacheStats(
                cache_id=self.registration.cache_id,
                owner=self.registration.owner,
                entries=len(self._store),
                bytes_used=sum(self._sizes.values()),
                maximum_bytes=self.registration.max_bytes,
                maximum_entries=self.registration.max_entries,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                ttl_seconds=self.registration.ttl_seconds,
                last_cleanup=self._last_cleanup,
            )

    def _evict(self, needed_entries: int, needed_bytes: int) -> None:
        while len(self._store) + needed_entries > self.registration.max_entries or (
            self.bytes_used() + needed_bytes > self.registration.max_bytes
        ):
            if not self._store:
                return
            oldest = min(self._expires, key=self._expires.get)
            self._discard(oldest)
            self._evictions += 1

    def _expire_stale(self) -> None:
        now = self._now()
        expired = [key for key, at in self._expires.items() if at < now]
        for key in expired:
            self._discard(key)
        if expired:
            self._last_cleanup = _now_iso()

    def _discard(self, key: str) -> None:
        self._store.pop(key, None)
        self._sizes.pop(key, None)
        self._expires.pop(key, None)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class CacheRegistry:
    def __init__(self, storage: Optional[PerformanceStorage] = None, *, now_provider=None) -> None:
        self._storage = storage
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._caches: Dict[str, Cache] = {}

    def register(self, registration: CacheRegistration) -> None:
        purpose = registration.purpose.lower()
        for blocked in NEVER_CACHE_PURPOSES:
            if blocked in purpose:
                raise ValueError("Cache purpose is security-sensitive and must not be cached: %s" % registration.purpose)
        with self._lock:
            cache = Cache(registration, now_provider=self._now)
            self._caches[registration.cache_id] = cache
            if self._storage is not None:
                self._persist(cache, initial=True)

    def cache(self, cache_id: str) -> Optional[Cache]:
        with self._lock:
            return self._caches.get(cache_id)

    def get(self, cache_id: str, key: str) -> Tuple[Optional[Any], bool]:
        cache = self.cache(cache_id)
        if cache is None:
            return None, False
        return cache.get(key)

    def put(self, cache_id: str, key: str, value: Any, size_bytes: int = 0) -> None:
        cache = self.cache(cache_id)
        if cache is None:
            raise ValueError("Cache is not registered: %s" % cache_id)
        cache.put(key, value, size_bytes)
        if self._storage is not None:
            self._persist(cache)

    def invalidate(self, cache_id: str, key: str = "") -> bool:
        cache = self.cache(cache_id)
        if cache is None:
            return False
        if key:
            cache.invalidate(key)
        else:
            cache.invalidate_all()
        if self._storage is not None:
            self._persist(cache)
        return True

    def invalidate_tag(self, tag: str) -> int:
        """Invalidate every cache that declares ``tag`` as an invalidation
        trigger (used by permission/trust/session/secret changes)."""
        count = 0
        with self._lock:
            caches = list(self._caches.values())
        for cache in caches:
            if tag in cache.registration.invalidation:
                cache.invalidate_all()
                count += 1
        return count

    def clear_safe(self) -> int:
        """Clear caches that are safe to clear (never security-sensitive)."""
        count = 0
        with self._lock:
            caches = list(self._caches.values())
        for cache in caches:
            if not cache.registration.security_sensitive:
                cache.invalidate_all()
                count += 1
        return count

    def unregister(self, cache_id: str) -> None:
        with self._lock:
            self._caches.pop(cache_id, None)
        if self._storage is not None:
            self._storage.delete_cache(cache_id)

    def stats(self) -> List[CacheStats]:
        with self._lock:
            caches = list(self._caches.values())
        return [cache.stats() for cache in caches]

    def _persist(self, cache: Cache, initial: bool = False) -> None:
        if self._storage is None:
            return
        registration = cache.registration
        stats = cache.stats()
        self._storage.upsert_cache({
            "cache_id": registration.cache_id,
            "owner": registration.owner,
            "purpose": registration.purpose,
            "scope": registration.scope,
            "max_entries": registration.max_entries,
            "max_bytes": registration.max_bytes,
            "ttl_seconds": registration.ttl_seconds,
            "privacy": registration.privacy,
            "invalidation": registration.invalidation,
            "persistence": registration.persistence,
            "encryption": registration.encryption,
            "sharing_policy": registration.sharing_policy,
            "failure_behavior": registration.failure_behavior,
            "security_sensitive": registration.security_sensitive,
            "entries": stats.entries,
            "bytes_used": stats.bytes_used,
            "hits": stats.hits,
            "misses": stats.misses,
            "evictions": stats.evictions,
            "last_cleanup": stats.last_cleanup or "",
        })
