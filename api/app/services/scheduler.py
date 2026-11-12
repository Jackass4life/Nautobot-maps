"""Background scheduler (#154, #165): sync and record alert history with nobody viewing.

Called as ``scheduler.function()`` so tests can replace it on this module.

Ported from nautobot_maps/scheduler.py (#602); on the event loop since #663.
"""

import asyncio
import threading
from datetime import UTC

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

# Every app process runs one scheduler task on its event loop (#649; the tick awaits
# its work since #663, upstream HTTP in threads); a PostgreSQL advisory lock makes
# sure only one of them (across all workers and containers) works per tick.  It runs
# the syncs that are due and, when one ran, rebuilds the alert board, which records
# alert history.  Without it, syncs and history only happened while someone had a
# page open.
MAX_TICK_SECONDS = 30
_started = False
_start_lock = threading.Lock()
_stop: asyncio.Event | None = None
_task: asyncio.Task | None = None
# Background tasks' names (`session.spawn`), as their threads had.
PUSH_REBUILD_TASK_NAME = "librenms-push-rebuild"
WIRELESS_TASK_NAME = "wireless-sync"
# At shutdown, a rebuild or wireless sync still running gets this long to finish.
SHUTDOWN_GRACE_SECONDS = 5


def tick_seconds() -> int:
    """Seconds between ticks: often enough to catch a due sync promptly."""
    intervals = [settings.INVENTORY_SYNC_INTERVAL_SECONDS]
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        intervals.append(settings.LIBRENMS_SYNC_INTERVAL_SECONDS)
    if settings.WIRELESS_CONTROLLERS:
        intervals.append(settings.WIRELESS_SYNC_INTERVAL_SECONDS)
    return max(1, min(MAX_TICK_SECONDS, *intervals))


async def prune_alert_history_if_due() -> None:
    """Daily history retention (#194); a failure is logged, never stops the syncs."""
    if settings.ALERT_HISTORY_RETENTION_DAYS <= 0:
        return
    try:
        async with aconnection.connect() as conn:
            await alerts.maybe_prune_alert_history(conn)
    except Exception as exc:
        logger.opt(exception=True).warning("Alert history retention failed: {}", exc)


async def tick() -> bool:
    """Run the due syncs and rebuild the board if one ran.  Returns whether it did work."""
    release = await aconnection.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # no database, or another process holds the tick
    try:
        # Level changes queue notifications, also from board builds that a
        # page request ran; only this (locked) tick sends them (#282).  Send
        # first, so a slow or failing sync below doesn't hold them up.
        await send_notifications()
        await prune_alert_history_if_due()
        worked = await inventory.ensure_snapshot(wait=True)
        # The wireless controllers (#508), as a task of their own: a slow or
        # unreachable one never holds up the tick, notifications or rebuilds.
        start_wireless_sync()
        # LibreNMS pushes (#284) and webhook alerts (#292) whose own rebuild
        # didn't get the lock.
        pushes = await pending_librenms_pushes()
        received = await pending_webhook_alerts()
        changed = await pending_changes()  # a change whose own rebuild lost the lock (#295)
        if worked or pushes or received or changed:
            # The syncs invalidated the cached board; rebuild it now so alert
            # history is recorded even if nobody opens the board.
            await rebuild_board()
            await ack_librenms_pushes(pushes)  # only after the rebuild succeeded
            await ack_webhook_alerts(received)
            await ack_changes(changed)
        return worked or bool(pushes) or received is not None or changed is not None
    finally:
        await release()


_wireless_running = threading.Lock()


async def sync_wireless() -> bool:
    """The controllers that are due (#508), under their own lock (one process
    at a time); when an AP went down or came back, the board is rebuilt as
    for any change.  A failure is logged, never raised."""
    release = await aconnection.try_advisory_lock("wireless_sync")
    if not callable(release):
        return False
    try:
        # Its HTTP in threads (#657).
        changed = await wireless.sync_due()
    except Exception as exc:
        logger.opt(exception=True).warning("Wireless controller sync failed: {}", exc)
        return False
    finally:
        await release()
    if changed:
        await board_changed()
    return changed


def start_wireless_sync() -> None:
    """Run ``sync_wireless`` in the background unless it is still running."""
    if not settings.WIRELESS_CONTROLLERS or not _wireless_running.acquire(blocking=False):
        return

    async def run() -> None:
        try:
            await sync_wireless()
        finally:
            _wireless_running.release()

    session.spawn(WIRELESS_TASK_NAME, run)


async def rebuild_board(trigger: str = "scheduler") -> None:
    """Build the board from the cache, store it, and send what the build queued."""
    await alerts.board(trigger, rebuild=True)
    await send_notifications()


async def board_changed() -> None:
    """A case, acknowledgement or maintenance window changed what the board
    shows: drop it, and with the scheduler running rebuild it now in the
    background, so the next page load finds it ready (#295)."""
    await asyncio.to_thread(caching.invalidate_alert_board)
    # Kept in the database: a rebuild that loses the lock to another one (or
    # a build that read before this change) leaves it pending for the next.
    await request_change_rebuild()
    if _started:
        rebuild_after_push(always=True, trigger="change")


CHANGE_REQUESTED_STATE = "board_change_requested"
CHANGE_REBUILT_STATE = "board_change_rebuilt"


async def _state_time(source: str, conn: aconnection.Connection):
    return timeutil.as_utc_datetime(
        (await inventory.sync_state(source, conn)).get("last_completed_at")
    )


async def request_change_rebuild() -> None:
    try:
        async with aconnection.connect() as conn, conn.transaction():
            await inventory.record_sync_state(
                conn, CHANGE_REQUESTED_STATE, last_completed_at=timeutil.iso_utc_now()
            )
    except Exception as exc:
        logger.opt(exception=True).warning("Recording a board change failed: {}", exc)


async def pending_changes():
    """When a change asked for a rebuild that no build has taken in, else None."""
    try:
        async with aconnection.connect() as conn:
            requested = await _state_time(CHANGE_REQUESTED_STATE, conn)
            rebuilt = await _state_time(CHANGE_REBUILT_STATE, conn)
        return (
            requested
            if requested is not None and (rebuilt is None or requested > rebuilt)
            else None
        )
    except Exception as exc:
        logger.opt(exception=True).warning("Reading pending board changes failed: {}", exc)
        return None


async def ack_changes(requested) -> None:
    if requested is None:
        return
    try:
        async with aconnection.connect() as conn, conn.transaction():
            await inventory.record_sync_state(
                conn,
                CHANGE_REBUILT_STATE,
                last_completed_at=requested.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            )
    except Exception as exc:
        logger.opt(exception=True).warning("Recording the board change rebuild failed: {}", exc)


async def pending_librenms_pushes() -> list[int]:
    try:
        async with aconnection.connect() as conn:
            return await inventory.pending_librenms_pushes(conn)
    except Exception as exc:
        logger.opt(exception=True).warning("Reading pending LibreNMS pushes failed: {}", exc)
        return []


async def ack_librenms_pushes(seqs: list[int]) -> None:
    if not seqs:
        return
    async with aconnection.connect() as conn:
        await inventory.ack_librenms_pushes(conn, seqs)


def rebuild_after_push(always: bool = False, trigger: str = "push") -> None:
    """Rebuild the board now for a LibreNMS push (#284), as a background task.  If
    another process holds the scheduler lock, its next tick does it.

    *always*: at least once, also without a pending LibreNMS push (an
    Alertmanager or webhook alert, #292).
    """

    async def run() -> None:
        release = await aconnection.try_advisory_lock("background_scheduler")
        if not callable(release):
            return
        try:
            # Again while pushes, webhook alerts or changes arrived during the
            # rebuild (bounded: the tick picks up anything left).  Each is
            # read before the build, so one arriving during it stays pending.
            for attempt in range(5):
                pushes = await pending_librenms_pushes()
                received = await pending_webhook_alerts()
                changed = await pending_changes()
                if not (pushes or received or changed) and not (attempt == 0 and always):
                    break
                await rebuild_board(trigger)
                await ack_librenms_pushes(pushes)
                await ack_webhook_alerts(received)
                await ack_changes(changed)
        except Exception as exc:
            logger.opt(exception=True).warning("Rebuild after a LibreNMS push failed: {}", exc)
        finally:
            await release()

    session.spawn(PUSH_REBUILD_TASK_NAME, run)


async def pending_webhook_alerts():
    """When webhook alerts (#292) arrived that no build has taken in, else None."""
    try:
        async with aconnection.connect() as conn:
            return await webhooks.pending_rebuild(conn)
    except Exception as exc:
        logger.opt(exception=True).warning("Reading pending webhook alerts failed: {}", exc)
        return None


async def ack_webhook_alerts(received) -> None:
    if received is None:
        return
    try:
        async with aconnection.connect() as conn:
            await webhooks.mark_rebuilt(conn, received)
    except Exception as exc:
        logger.opt(exception=True).warning("Recording the webhook rebuild failed: {}", exc)


async def send_notifications() -> None:
    """A failure is logged, never stops the tick or a rebuild."""
    try:
        await notify.send_pending()
    except Exception as exc:
        logger.opt(exception=True).warning("Sending notifications failed: {}", exc)


async def loop(stop_event: asyncio.Event) -> None:
    """Tick until *stop_event*: the tick awaits its work, so the event loop keeps
    answering requests meanwhile (#649, #663). Each worker its own stop event (#601
    review): a restart can't cancel the previous worker's shutdown."""
    while not stop_event.is_set():
        try:
            await tick()
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


def stop() -> asyncio.Task | None:
    """At shutdown (the app's lifespan, #600): the task is cancelled now; a tick running
    is cancelled with it (its transactions roll back, its locks are released). Returns
    the cancelled task, for the lifespan to let it finish."""
    global _started, _task
    with _start_lock:
        if _stop is not None:
            _stop.set()
        task, _task = _task, None
        if task is not None:
            task.cancel()
        _started = False
    return task


async def shutdown() -> None:
    """The lifespan's end (#663): the scheduler stopped, and the rebuilds and wireless
    syncs still running given SHUTDOWN_GRACE_SECONDS, then cancelled."""
    task = stop()
    if task is not None and task.get_loop() is asyncio.get_running_loop():
        await asyncio.wait([task], timeout=SHUTDOWN_GRACE_SECONDS)
    await session.finish_background(SHUTDOWN_GRACE_SECONDS)
