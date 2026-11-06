"""PostgreSQL advisory lock keys (#598): the current app's ``db.advisory_lock_key``,
so the same name is the same lock in both apps during the switchover."""

import hashlib


def advisory_lock_key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)
