"""Physical modifier-key release wait (Layer 0 of the capture cascade)."""
from windows_integration.keys import (
    is_key_down,
    modifiers_held,
    wait_modifiers_released,
)


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.001)


def test_returns_immediately_when_nothing_held():
    ft = FakeTime()
    assert wait_modifiers_released(
        timeout=1.5, sleep=ft.sleep, clock=ft.monotonic, still_down=lambda: False
    ) is True
    assert ft.t < 0.1  # no wasted delay


def test_waits_until_modifiers_are_released():
    ft = FakeTime()

    def still_down():
        return ft.t < 0.2  # "user" releases the keys after 200 ms

    assert wait_modifiers_released(
        timeout=1.5, sleep=ft.sleep, clock=ft.monotonic, still_down=still_down
    ) is True
    assert ft.t >= 0.2  # really did wait for the release


def test_gives_up_after_timeout_when_keys_never_release():
    ft = FakeTime()
    assert wait_modifiers_released(
        timeout=0.5, sleep=ft.sleep, clock=ft.monotonic, still_down=lambda: True
    ) is False
    assert ft.t >= 0.5  # waited, then gave up (capture must not hang)


def test_real_keyboard_state_is_readable():
    """The ctypes plumbing must work on a real desktop (no exceptions)."""
    assert isinstance(modifiers_held(), bool)
    assert isinstance(is_key_down(0x11), bool)  # VK_CONTROL
    assert is_key_down(0x11) == modifiers_held() or True  # just no crash
