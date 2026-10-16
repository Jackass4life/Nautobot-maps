"""Background scheduler (#154, #165): sync and record alert history with nobody viewing.

Called as ``scheduler.function()`` so tests can replace it on this module.
"""

import logging
import threading
from datetime import UTC

from nautobot_maps import alerts, caching, db, inventory, notify, settings, timeutil, webhooks

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
PUSH_REBUILD_THREAD_NAME = "librenms-push-rebuild"


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
        # LibreNMS pushes (#284) and webhook alerts (#292) whose own rebuild
        # didn't get the lock.
        pushes = pending_librenms_pushes()
        received = pending_webhook_alerts()
        changed = pending_changes()  # a change whose own rebuild lost the lock (#295)
        if worked or pushes or received or changed:
            # The syncs invalidated the cached board; rebuild it now so alert
            # history is recorded even if nobody opens the board.
            rebuild_board()
            ack_librenms_pushes(pushes)  # only after the rebuild succeeded
            ack_webhook_alerts(received)
            ack_changes(changed)
        return worked or bool(pushes) or received is not None or changed is not None
    finally:
        release()


def rebuild_board(trigger: str = "scheduler") -> None:
    """Build the board from the cache, store it, and send what the build queued."""
    alerts.board(trigger, rebuild=True)
    send_notifications()


def board_changed() -> None:
    """A case, acknowledgement or maintenance window changed what the board
    shows: drop it, and with the scheduler running rebuild it now in the
    background, so the next page load finds it ready (#295)."""
    caching.invalidate_alert_board()
    # Kept in the database: a rebuild that loses the lock to another one (or
    # a build that read before this change) leaves it pending for the next.
    request_change_rebuild()
    if _started:
        rebuild_after_push(always=True, trigger="change")


CHANGE_REQUESTED_STATE = "board_change_requested"
CHANGE_REBUILT_STATE = "board_change_rebuilt"


def _state_time(source: str, conn):
    return timeutil.as_utc_datetime(inventory.get_sync_state(source, conn=conn).get("last_completed_at"))


def request_change_rebuild() -> None:
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            inventory.record_sync_state(conn, CHANGE_REQUESTED_STATE, last_completed_at=timeutil.iso_utc_now())
    except Exception as exc:
        logger.warning("Recording a board change failed: %s", exc, exc_info=True)
    finally:
        conn.close()


def pending_changes():
    """When a change asked for a rebuild that no build has taken in, else None."""
    conn = db.get_conn()
    if conn is None:
        return None
    try:
        requested = _state_time(CHANGE_REQUESTED_STATE, conn)
        rebuilt = _state_time(CHANGE_REBUILT_STATE, conn)
        return requested if requested is not None and (rebuilt is None or requested > rebuilt) else None
    except Exception as exc:
        logger.warning("Reading pending board changes failed: %s", exc, exc_info=True)
        return None
    finally:
        conn.close()


def ack_changes(requested) -> None:
    if requested is None:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            inventory.record_sync_state(
                conn,
                CHANGE_REBUILT_STATE,
                last_completed_at=requested.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            )
    except Exception as exc:
        logger.warning("Recording the board change rebuild failed: %s", exc, exc_info=True)
    finally:
        conn.close()


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


def rebuild_after_push(always: bool = False, trigger: str = "push") -> None:
    """Rebuild the board now for a LibreNMS push (#284), in a thread.  If
    another process holds the scheduler lock, its next tick does it.

    *always*: at least once, also without a pending LibreNMS push (an
    Alertmanager or webhook alert, #292).
    """

    def run() -> None:
        release = db.try_advisory_lock("background_scheduler")
        if not callable(release):
            return
        try:
            # Again while pushes, webhook alerts or changes arrived during the
            # rebuild (bounded: the tick picks up anything left).  Each is
            # read before the build, so one arriving during it stays pending.
            for attempt in range(5):
                pushes = pending_librenms_pushes()
                received = pending_webhook_alerts()
                changed = pending_changes()
                if not (pushes or received or changed) and not (attempt == 0 and always):
                    break
                rebuild_board(trigger)
                ack_librenms_pushes(pushes)
                ack_webhook_alerts(received)
                ack_changes(changed)
        except Exception as exc:
            logger.warning("Rebuild after a LibreNMS push failed: %s", exc, exc_info=True)
        finally:
            release()

    threading.Thread(target=run, name=PUSH_REBUILD_THREAD_NAME, daemon=True).start()


def pending_webhook_alerts():
    """When webhook alerts (#292) arrived that no build has taken in, else None."""
    conn = db.get_conn()
    if conn is None:
        return None
    try:
        return webhooks.pending_rebuild(conn)
    except Exception as exc:
        logger.warning("Reading pending webhook alerts failed: %s", exc, exc_info=True)
        return None
    finally:
        conn.close()


def ack_webhook_alerts(received) -> None:
    if received is None:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        webhooks.mark_rebuilt(conn, received)
    except Exception as exc:
        logger.warning("Recording the webhook rebuild failed: %s", exc, exc_info=True)
    finally:
        conn.close()


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
