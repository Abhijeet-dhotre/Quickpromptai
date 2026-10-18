"""Token-elevation detection against real Win32 APIs."""
import os

from windows_integration.elevation import is_process_elevated


def test_own_process_elevation_is_determinable():
    result = is_process_elevated(os.getpid())
    assert result in (True, False)  # our own process must always resolve


def test_pid_zero_is_unknown():
    assert is_process_elevated(0) is None


def test_nonexistent_pid_is_unknown_not_a_crash():
    assert is_process_elevated(0x7FFFFFFF) is None
