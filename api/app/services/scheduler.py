"""Background scheduler (#154, #165): sync and record alert history with nobody viewing.

Called as ``scheduler.function()`` so tests can replace it on this module.

Ported from nautobot_maps/scheduler.py (#602): only the imports changed.
"""

import asyncio
import threading
from datetime import UTC

import anyio.to_thread
from loguru import logger

from app.clients import caching
from app.core.legacy_settings import settings
from app.db import aconnection, session
from app.db import connection as db
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import notify_service as notify
from app.services import webhooks_service as webhooks
from app.services import wireless_service as wireless
from app.utils import timeutil

# Every app process runs one scheduler task (on its event loop since #649, each
# tick's work in a thread while it is sync, reaching the loop for what is async);
# a PostgreSQL advisory lock makes sure only one of them (across all workers and
# containers) works per tick.  It runs the syncs that are due and, when one ran, rebuilds the alert
# board, which records alert history.  Without it, syncs and history only
# happened while someone had a page open.
MAX_TICK_SECONDS = 30
_started = False
_start_lock = threading.Lock()
_stop: asyncio.Event | None = None
_task: asyncio.Task | None = None
PUSH_REBUILD_THREAD_NAME = "librenms-push-rebuild"


def tick_seconds() -> int:
    """Seconds between ticks: often enough to catch a due sync promptly."""
    intervals = [settings.INVENTORY_SYNC_INTERVAL_SECONDS]
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        intervals.append(settings.LIBRENMS_SYNC_INTERVAL_SECONDS)
    if settings.WIRELESS_CONTROLLERS:
        intervals.append(settings.WIRELESS_SYNC_INTERVAL_SECONDS)
    return max(1, min(MAX_TICK_SECONDS, *intervals))


async def _prune_alert_history() -> None:
    async with aconnection.connect() as conn:
        await alerts.maybe_prune_alert_history(conn)


def prune_alert_history_if_due() -> None:
    """Daily history retention (#194); a failure is logged, never stops the syncs."""
    if settings.ALERT_HISTORY_RETENTION_DAYS <= 0:
        return
    try:
        # On the event loop (#661): the pruning's SQL is async.
        session.from_sync(_prune_alert_history)
    except Exception as exc:
        logger.opt(exception=True).warning("Alert history retention failed: {}", exc)


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
        # The wireless controllers (#508), on their own thread: a slow or
        # unreachable one never holds up the tick, notifications or rebuilds.
        start_wireless_sync()
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


WIRELESS_THREAD_NAME = "wireless-sync"
_wireless_running = threading.Lock()


def sync_wireless() -> bool:
    """The controllers that are due (#508), under their own lock (one process
    at a time); when an AP went down or came back, the board is rebuilt as
    for any change.  A failure is logged, never raised."""
    release = db.try_advisory_lock("wireless_sync")
    if not callable(release):
        return False
    try:
        # On the event loop (#657): the sync's SQL is async, its HTTP in threads.
        changed = session.from_sync(wireless.sync_due)
    except Exception as exc:
        logger.opt(exception=True).warning("Wireless controller sync failed: {}", exc)
        return False
    finally:
        release()
    if changed:
        board_changed()
    return changed


def start_wireless_sync() -> None:
    """Run ``sync_wireless`` in the background unless it is still running."""
    if not settings.WIRELESS_CONTROLLERS or not _wireless_running.acquire(blocking=False):
        return

    def run() -> None:
        try:
            sync_wireless()
        finally:
            _wireless_running.release()

    threading.Thread(target=run, name=WIRELESS_THREAD_NAME, daemon=True).start()


def rebuild_board(trigger: str = "scheduler") -> None:
    """Build the board from the cache, store it, and send what the build queued."""
    # On the event loop (#661): the build is async.
    session.from_sync(alerts.board, trigger, rebuild=True)
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
    return timeutil.as_utc_datetime(
        inventory.get_sync_state(source, conn=conn).get("last_completed_at")
    )


def request_change_rebuild() -> None:
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            inventory.record_sync_state(
                conn, CHANGE_REQUESTED_STATE, last_completed_at=timeutil.iso_utc_now()
            )
    except Exception as exc:
        logger.opt(exception=True).warning("Recording a board change failed: {}", exc)
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
        return (
            requested
            if requested is not None and (rebuilt is None or requested > rebuilt)
            else None
        )
    except Exception as exc:
        logger.opt(exception=True).warning("Reading pending board changes failed: {}", exc)
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
        logger.opt(exception=True).warning("Recording the board change rebuild failed: {}", exc)
    finally:
        conn.close()


def pending_librenms_pushes() -> list[int]:
    conn = db.get_conn()
    if conn is None:
        return []
    try:
        return inventory.pending_librenms_pushes(conn)
    except Exception as exc:
        logger.opt(exception=True).warning("Reading pending LibreNMS pushes failed: {}", exc)
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
            logger.opt(exception=True).warning("Rebuild after a LibreNMS push failed: {}", exc)
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
        logger.opt(exception=True).warning("Reading pending webhook alerts failed: {}", exc)
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
        logger.opt(exception=True).warning("Recording the webhook rebuild failed: {}", exc)
    finally:
        conn.close()


def send_notifications() -> None:
    """On the event loop, from the tick's or a rebuild's thread (#655): the sending is async."""
    try:
        session.from_sync(notify.send_pending)
    except Exception as exc:
        logger.opt(exception=True).warning("Sending notifications failed: {}", exc)


async def loop(stop_event: asyncio.Event) -> None:
    """Tick until *stop_event*: the tick's work in a thread, so the event loop keeps
    answering requests meanwhile (#649). Each worker its own stop event (#601 review): a
    restart can't cancel the previous worker's shutdown."""
    while not stop_event.is_set():
        try:
            # anyio's thread (#655): the tick reaches the loop with session.from_sync.
            # Abandoned on cancel: shutdown doesn't wait for a tick that is running.
            await anyio.to_thread.run_sync(tick, abandon_on_cancel=True)
        except Exception as exc:
            logger.opt(exception=True).warning("Background scheduler tick failed: {}", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), tick_seconds())
        except TimeoutError:
            pass


def start() -> bool:
    """Start this process's scheduler task once, on the running event loop (the app's
    lifespan). Returns whether it runs."""
    global _started, _stop, _task
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
        _stop = asyncio.Event()
        _task = asyncio.get_running_loop().create_task(loop(_stop), name="background-scheduler")
    logger.info("Background scheduler started (tick every {}s)", tick_seconds())
    return True


def stop() -> None:
    """At shutdown (the app's lifespan, #600): the task ends now. A tick already running
    finishes in its thread, as the scheduler's daemon thread did; shutdown doesn't wait
    for it."""
    global _started, _task
    with _start_lock:
        if _stop is not None:
            _stop.set()
        if _task is not None:
            _task.cancel()
            _task = None
        _started = False
