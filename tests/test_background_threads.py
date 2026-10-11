"""Tests wait for the app's background threads before the next test (#299)."""

import threading

from nautobot_maps import inventory
from tests.conftest import join_background_threads


def test_a_thread_still_running_after_the_wait_is_reported():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, args=(5,), name=inventory.SYNC_THREAD_NAME, daemon=True)
    thread.start()
    try:
        assert join_background_threads(timeout=0.05) == [inventory.SYNC_THREAD_NAME]
    finally:
        stop.set()
    assert join_background_threads() == []


def test_other_threads_are_not_waited_for():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, args=(5,), name="background-scheduler", daemon=True)
    thread.start()
    try:
        assert join_background_threads(timeout=0.05) == []
    finally:
        stop.set()
        thread.join()
