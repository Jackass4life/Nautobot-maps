"""Background scheduler (#154, #600): sync with nobody viewing, as the current app's
nautobot_maps/scheduler.py.

Every app process runs one scheduler thread; a PostgreSQL advisory lock
(``background_scheduler``, the same key as the current app's) makes sure only one
of them, across all workers, containers and both apps, works per tick. It runs the
syncs that are due.

The current app's tick also rebuilds the alert board after a sync (recording alert
history), sends queued notifications, prunes history, and takes in LibreNMS pushes,
webhook alerts and wireless controllers; each joins here with its area (#587).
Called as ``scheduler.function()`` so tests can replace it on this module.
"""

import logging
import threading

from app import db
from app.services import inventory_service as inventory
from app.utils.legacy_settings import settings

logger = logging.getLogger(__name__)

MAX_TICK_SECONDS = 30
_started = False
_start_lock = threading.Lock()
_stop = threading.Event()


def tick_seconds() -> int:
    """Seconds between ticks: often enough to catch a due sync promptly."""
    intervals = [settings.INVENTORY_SYNC_INTERVAL_SECONDS]
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        intervals.append(settings.LIBRENMS_SYNC_INTERVAL_SECONDS)
    return max(1, min(MAX_TICK_SECONDS, *intervals))


def tick() -> bool:
    """Run the due syncs. Returns whether one ran."""
    release = db.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # another process holds the tick
    try:
        return bool(inventory.ensure_snapshot(wait=True))
    finally:
        release()


def loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception as exc:
            logger.warning("Background scheduler tick failed: %s", exc, exc_info=True)
        _stop.wait(tick_seconds())


def start() -> bool:
    """Start this process's scheduler thread once. Returns whether it runs."""
    global _started
    if not settings.BACKGROUND_SYNC_ENABLED or not (
        settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN
    ):
        return False
    with _start_lock:
        if _started:
            return True
        _started = True
        _stop.clear()
        threading.Thread(target=loop, name="background-scheduler", daemon=True).start()
    logger.info("Background scheduler started (tick every %ss)", tick_seconds())
    return True


def stop() -> None:
    """At shutdown: the thread ends after its tick."""
    global _started
    _stop.set()
    _started = False
