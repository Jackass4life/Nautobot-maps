"""Background scheduler (#154, #165): sync and record alert history with nobody viewing.

Called as ``scheduler.function()`` so tests can replace it on this module.
"""

import logging
import threading

from nautobot_maps import alerts, caching, db, inventory, maintenance, notify, settings

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


def prune_alert_history_if_due() -> None:
    """Daily history retention (#194); a failure is logged, never stops the syncs."""
    if settings.ALERT_HISTORY_RETENTION_DAYS <= 0:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        alerts.maybe_prune_alert_history(conn)
    except Exception as exc:
        logger.warning("Alert history retention failed: %s", exc, exc_info=True)
    finally:
        conn.close()


def tick() -> bool:
    """Run the due syncs and rebuild the board if one ran.  Returns whether it did work."""
    release = db.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # no database, or another process holds the tick
    try:
        # Level changes queue notifications, also from board builds that a
        # page request ran; only this (locked) tick sends them (#282).  Send
        # first, so a slow or failing sync below doesn't hold them up.
        send_notifications()
        prune_alert_history_if_due()
        worked = inventory.ensure_snapshot(wait=True)
        # LibreNMS pushes (#284) whose own rebuild didn't get the lock.
        pushes = pending_librenms_pushes()
        if worked or pushes:
            # The syncs invalidated the cached board; rebuild it now so alert
            # history is recorded even if nobody opens the board.
            rebuild_board()
            ack_librenms_pushes(pushes)  # only after the rebuild succeeded
        return worked or bool(pushes)
    finally:
        release()


def rebuild_board() -> None:
    """Build the board from the cache, store it, and send what the build queued."""
    payload = alerts.build_alert_board_payload(snapshot_only=True)
    caching.set(
        "alert-board-data:v3",
        payload,
        timeout=maintenance.cache_seconds(payload.get("next_maintenance_change"), settings.CACHE_TTL),
    )
    send_notifications()


def pending_librenms_pushes() -> list[int]:
    conn = db.get_conn()
    if conn is None:
        return []
    try:
        return inventory.pending_librenms_pushes(conn)
    except Exception as exc:
        logger.warning("Reading pending LibreNMS pushes failed: %s", exc, exc_info=True)
        return []
    finally:
        conn.close()


def ack_librenms_pushes(seqs: list[int]) -> None:
    if not seqs:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        inventory.ack_librenms_pushes(conn, seqs)
    finally:
        conn.close()


def rebuild_after_push() -> None:
    """Rebuild the board now for a LibreNMS push (#284), in a thread.  If
    another process holds the scheduler lock, its next tick does it."""

    def run() -> None:
        release = db.try_advisory_lock("background_scheduler")
        if not callable(release):
            return
        try:
            # Again while pushes arrived during the rebuild (bounded: the
            # tick picks up anything left).
            for _ in range(5):
                pushes = pending_librenms_pushes()
                if not pushes:
                    break
                rebuild_board()
                ack_librenms_pushes(pushes)
        except Exception as exc:
            logger.warning("Rebuild after a LibreNMS push failed: %s", exc, exc_info=True)
        finally:
            release()

    threading.Thread(target=run, name="librenms-push-rebuild", daemon=True).start()


def send_notifications() -> None:
    try:
        notify.send_pending()
    except Exception as exc:
        logger.warning("Sending notifications failed: %s", exc, exc_info=True)


def loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception as exc:
            logger.warning("Background scheduler tick failed: %s", exc, exc_info=True)
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
