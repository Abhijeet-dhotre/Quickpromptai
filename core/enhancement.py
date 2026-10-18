"""Prompt enhancement engine.

Builds the system instruction and user message sent to the configured AI
provider. The engine has three modes and an optional model-specific
formatting strategy. It never executes the task itself; it only produces an
improved, ready-to-copy prompt that preserves the user's intent.
"""
from __future__ import annotations

from .errors import InputTooLongError
from .models import EnhanceMode

# --------------------------------------------------------------------------
# Core instruction shared by every mode
# --------------------------------------------------------------------------

CORE_INSTRUCTION = """\
You are QuickPrompt AI, an expert prompt engineer. You transform rough notes,
half-finished ideas, and ordinary text into clear, high-quality prompts for AI
assistants. You return ONLY the improved prompt — you never perform the task
the prompt describes.

Non-negotiable rules:
- Preserve the user's original intent and every explicit requirement. Never
  drop, weaken, or contradict anything the user asked for.
- Correct spelling, grammar, punctuation, and unclear or ambiguous wording.
- Identify the actual task and the expected outcome; make the goal explicit.
- Do not invent facts, data, datasets, APIs, credentials, statistics, URLs,
  citations, or requirements that were not present or strongly implied.
- Never fabricate research results or sources. If the text makes claims,
  keep them as claims — do not add new "facts".
- Avoid padding, repetition, rhetorical fluff, and unnecessary length.
- Keep user-provided details exactly (programming languages, file names,
  formats, constraints, names, numbers).
- Use the same language as the user's text unless the text explicitly targets
  a different language.
- Ask at most one short clarifying question block, and only when essential
  information is genuinely missing. Otherwise make safe assumptions and state
  them briefly inside the prompt.
- Output only the final prompt text: no preamble, no commentary, no markdown
  fences or quotation marks around it, no "Here is the prompt".
"""

# --------------------------------------------------------------------------
# Mode-specific instructions
# --------------------------------------------------------------------------

MODE_INSTRUCTIONS: dict[EnhanceMode, str] = {
    EnhanceMode.QUICK_FIX: """\
Mode: Quick Fix.
Make minimal, surgical edits only:
- Fix spelling, grammar, punctuation, and clearly broken wording.
- Keep the original structure, tone, register, and approximate length.
- Do not restructure, expand, or add new requirements, context, or sections.
- If a sentence is ambiguous, prefer the interpretation closest to the
  original wording rather than rewriting it.
The result should read like the user's own text, just correctly written.""",

    EnhanceMode.PROMPT_ENHANCE: """\
Mode: Prompt Enhance.
Turn the text into a structured, effective prompt:
- State the task and desired outcome clearly in one or two opening lines.
- Include only the context that is necessary to perform the task.
- Specify the expected output format (structure, length, language, medium).
- Add sensible constraints and requirements that are directly implied by the
  original text — never invent new ones.
- Use short labeled sections or a compact bullet list when it improves
  clarity; skip headings for very simple prompts.
- Resolve obvious gaps with brief, clearly stated assumptions instead of
  questions wherever possible.""",

    EnhanceMode.ADVANCED_OPTIMIZE: """\
Mode: Advanced Optimize.
Produce a fully optimized prompt with explicit sections:
- Objective — the task and the definition of a good result.
- Context — everything needed to perform the task, no more.
- Requirements — numbered, testable requirements, including all explicit
  user requirements.
- Constraints — limits, style, tone, technologies, out-of-scope items.
- Output Format — the exact shape of the answer (files, sections, format,
  language).
- Success Criteria — how to judge the result.
- Add a Verification/Testing section for tasks where checking the result is
  meaningful.
Task-type adjustments:
- Coding: specify the programming language, dependencies, expected files or
  modules, error handling expectations, and how the code should be tested.
- Research/analysis: require evidence to be separated from assumptions and
  unverified claims; ask for uncertainty to be stated instead of hidden.
- Writing/creative: specify audience, voice, length, and format.
Keep every section necessary; merge or omit sections that add no value."""
}

# --------------------------------------------------------------------------
# Optional model-specific formatting strategies (best-effort adaptation)
# --------------------------------------------------------------------------

MODEL_STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "universal": "",
    "chatgpt": """\
Formatting strategy (ChatGPT-style models):
- Use concise Markdown with short bold labels for sections and compact
  bullet lists for requirements.
- State the desired response format explicitly near the end.""",
    "claude": """\
Formatting strategy (Claude-style models):
- Use clearly delimited sections with explicit headings; keep instructions
  literal and unambiguous.
- Delimit any quoted reference material the prompt refers to with clear
  markers so it is not confused with instructions.""",
    "gemini": """\
Formatting strategy (Gemini-style models):
- Use numbered steps and explicit headings; keep each instruction
  self-contained so no step depends on unstated context.""",
}

#: Sentinel: pick the strategy from the provider that will run the prompt.
STRATEGY_AUTO = "auto"


def system_instruction(mode: EnhanceMode, model_strategy: str = "universal") -> str:
    """Compose the full system instruction for a mode and formatting strategy."""
    parts = [CORE_INSTRUCTION, MODE_INSTRUCTIONS[mode]]
    if model_strategy == STRATEGY_AUTO:
        # "auto" means universal formatting — never assume a specific vendor.
        model_strategy = "universal"
    strategy = MODEL_STRATEGY_INSTRUCTIONS.get(model_strategy, "")
    if strategy:
        parts.append(strategy)
    return "\n\n".join(parts)


def build_messages(text: str, mode: EnhanceMode,
                   model_strategy: str = "universal") -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for a request.

    The user message repeats the enhancement directives so the request still
    works with endpoints that ignore or truncate system prompts.
    """
    system_prompt = system_instruction(mode, model_strategy)
    user_prompt = (
        "Enhance the following text according your role and the selected "
        "mode.\n\n--- BEGIN TEXT ---\n"
        f"{text}\n"
        "--- END TEXT ---\n\n"
        "Return only the enhanced prompt."
    )
    return system_prompt, user_prompt


def validate_input(text: str, max_chars: int = 24000) -> str:
    """Validate and normalize input text before sending it to a provider."""
    cleaned = (text or "").strip()
    if not cleaned:
        from .errors import EmptySelectionError
        raise EmptySelectionError()
    if max_chars and len(cleaned) > max_chars:
        raise InputTooLongError(
            f"The text is {len(cleaned):,} characters long, which exceeds the "
            f"limit of {max_chars:,}. Shorten it or lower/raise the limit in "
            "Settings → Provider."
        )
    return cleaned
