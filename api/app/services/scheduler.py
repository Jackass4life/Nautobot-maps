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
from app.utils import timeutil
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


# The current app's markers (nautobot_maps/scheduler.py): a rebuild requested and the
# last one done; its scheduler rebuilds the board when the request is newer.
CHANGE_REQUESTED_STATE = "board_change_requested"


def request_change_rebuild() -> None:
    """Ask for a board rebuild, as the current app's ``request_change_rebuild``."""
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.record_sync_state(
                conn, CHANGE_REQUESTED_STATE, last_completed_at=timeutil.iso_utc_now()
            )
    except Exception as exc:
        logger.warning("Recording a board rebuild request failed: %s", exc, exc_info=True)
    finally:
        conn.close()


def tick() -> bool:
    """Run the due syncs. Returns whether one ran.

    The board isn't rebuilt here yet (it comes with the board, #602). A sync that ran
    asks for a rebuild instead (#601 review): with the database shared with the current
    app, its scheduler then rebuilds the board (alert history, notifications), which it
    would otherwise skip, finding no sync due."""
    release = db.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # another process holds the tick
    try:
        worked = bool(inventory.ensure_snapshot(wait=True))
        if worked:
            request_change_rebuild()
        return worked
    finally:
        release()


def loop(stop_event: threading.Event | None = None) -> None:
    stop_event = stop_event or _stop
    while not stop_event.is_set():
        try:
            tick()
        except Exception as exc:
            logger.warning("Background scheduler tick failed: %s", exc, exc_info=True)
        stop_event.wait(tick_seconds())


def start() -> bool:
    """Start this process's scheduler thread once. Returns whether it runs."""
    global _started, _stop
    if not settings.BACKGROUND_SYNC_ENABLED or not (
        settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN
    ):
        return False
    with _start_lock:
        if _started:
            return True
        _started = True
        _stop = threading.Event()
        threading.Thread(
            target=loop, args=(_stop,), name="background-scheduler", daemon=True
        ).start()
    logger.info("Background scheduler started (tick every %ss)", tick_seconds())
    return True


def stop() -> None:
    """At shutdown: the thread ends after its tick."""
    global _started
    with _start_lock:
        _stop.set()
        _started = False
