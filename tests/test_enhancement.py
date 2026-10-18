"""Tests for the prompt enhancement engine."""
import pytest

from core.enhancement import (
    CORE_INSTRUCTION,
    MODE_INSTRUCTIONS,
    STRATEGY_AUTO,
    build_messages,
    system_instruction,
    validate_input,
)
from core.errors import EmptySelectionError, InputTooLongError
from core.models import EnhanceMode


def test_all_modes_have_instructions():
    assert set(MODE_INSTRUCTIONS) == set(EnhanceMode)


def test_core_instruction_contains_safety_rules():
    text = CORE_INSTRUCTION.lower()
    assert "preserve the user's original intent" in text
    assert "never perform the task" in text
    assert "do not invent facts" in text
    assert "only the improved prompt" in text or "output only the final prompt" in text


def test_mode_instructions_differ():
    quick = MODE_INSTRUCTIONS[EnhanceMode.QUICK_FIX]
    prompt = MODE_INSTRUCTIONS[EnhanceMode.PROMPT_ENHANCE]
    advanced = MODE_INSTRUCTIONS[EnhanceMode.ADVANCED_OPTIMIZE]
    assert quick != prompt != advanced
    assert "minimal" in quick.lower()
    assert "output format" in prompt.lower()
    assert "success criteria" in advanced.lower()
    # Advanced mode must handle coding + research tasks per the spec.
    assert "coding" in advanced.lower()
    assert "research" in advanced.lower()


def test_system_instruction_includes_mode_and_strategy():
    instr = system_instruction(EnhanceMode.ADVANCED_OPTIMIZE, "claude")
    assert MODE_INSTRUCTIONS[EnhanceMode.ADVANCED_OPTIMIZE] in instr
    assert "Claude-style" in instr
    assert CORE_INSTRUCTION in instr


def test_universal_strategy_adds_nothing():
    base = system_instruction(EnhanceMode.QUICK_FIX, "universal")
    assert "strategy" not in base.lower().split("mode:")[0]


def test_auto_strategy_falls_back_to_universal():
    assert system_instruction(EnhanceMode.QUICK_FIX, STRATEGY_AUTO) == \
        system_instruction(EnhanceMode.QUICK_FIX, "universal")


def test_build_messages_contains_original_text():
    system, user = build_messages("write a script to sort files", EnhanceMode.QUICK_FIX)
    assert "write a script to sort files" in user
    assert "BEGIN TEXT" in user and "END TEXT" in user
    assert len(system) > 500


def test_validate_input_ok():
    assert validate_input("  hello  ") == "hello"


def test_validate_input_empty():
    with pytest.raises(EmptySelectionError):
        validate_input("   ")


def test_validate_input_too_long():
    with pytest.raises(InputTooLongError):
        validate_input("x" * 500, max_chars=100)
