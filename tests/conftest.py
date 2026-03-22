"""conftest.py – shared pytest configuration.

Adds the `demo/` directory to sys.path so that `mock_nautobot` can be imported
by the integration tests without any sys.path manipulation inside the test
modules themselves.
"""
import os
import sys

# Make demo/ importable
_demo_dir = os.path.join(os.path.dirname(__file__), "..", "demo")
if _demo_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_demo_dir))
