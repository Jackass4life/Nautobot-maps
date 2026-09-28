"""Background scheduler (#154, #165): sync and record alert history with nobody viewing.

Called as ``scheduler.function()`` so tests can replace it on this module.
"""

import logging
import threading

from nautobot_maps import alerts, caching, db, inventory, settings

logger = logging.getLogger(__name__)

# Every app process runs one scheduler thread; a PostgreSQL advisory lock
# makes sure only one of them (across all workers and containers) works per
# tick.  It runs the syncs that are due and, when one ran, rebuilds the alert
# board, which records alert history.  Without it, syncs and history only
# happened while someone had a page open.
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
    """Run the due syncs and rebuild the board if one ran.  Returns whether it did work."""
    release = db.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # no database, or another process holds the tick
    try:
        if not inventory.ensure_snapshot(wait=True):
            return False
        # The syncs invalidated the cached board; rebuild it now so alert
        # history is recorded even if nobody opens the board.
        payload = alerts.build_alert_board_payload(snapshot_only=True)
        caching.set("alert-board-data:v3", payload, timeout=settings.CACHE_TTL)
        return True
    finally:
        release()


def loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception as exc:
            logger.warning("Background scheduler tick failed: %s", exc)
        _stop.wait(tick_seconds())


def start() -> bool:
    """Start this process's scheduler thread once.  Returns whether it runs."""
    global _started
    if (
        not settings.BACKGROUND_SYNC_ENABLED
        or not db.dialect()
        or not (settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN)
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
