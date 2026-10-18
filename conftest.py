"""Pytest configuration: make project root importable and Qt headless-safe."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Run Qt tests headless (no real display required).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
