"""#126: the runtime timeout must cover the token budget it is given.

The advisor fell back to a deterministic snapshot while the AI runtime was
working perfectly. Two constants set independently contradicted each other: the
agentic adapter allowed 90s, while the answer budget was 2400 tokens, which at
the box's measured ~17 tok/s needs ~140s. Every answer that used its budget was
aborted mid-generation, retried twice into the same wall, and reported as
"runtime unavailable" after about five minutes.

These pin the relationship rather than the numbers, so raising the budget cannot
quietly reintroduce the same contradiction.
"""

import pytest

from family_cfo_api.ai_runtime_selection import (
    MIN_EXPECTED_TOKENS_PER_SECOND,
    RUNTIME_OVERHEAD_SECONDS,
    timeout_for_budget,
)

#: What the box actually does, measured on its GPU with the main reasoning
#: model. The floor in the source sits below this so ordinary variance is fine.
MEASURED_TOKENS_PER_SECOND = 17.2

#: The advisor's answer budget (api.chat._ANSWER_MAX_TOKENS).
ANSWER_MAX_TOKENS = 2400


def test_the_budget_that_broke_it_now_fits() -> None:
    """2400 tokens at the measured rate takes ~140s. The old hand-set 90s did
    not cover that; the derived timeout must, with room to spare."""
    needed = ANSWER_MAX_TOKENS / MEASURED_TOKENS_PER_SECOND
    allowed = timeout_for_budget(ANSWER_MAX_TOKENS)
    assert needed > 90, "the 90s the code used to allow was already too small"
    assert allowed > needed, f"{allowed:.0f}s must cover the {needed:.0f}s a full answer takes"


def test_the_timeout_grows_with_the_budget() -> None:
    """The point of deriving it: the two cannot drift apart again."""
    assert timeout_for_budget(4800) > timeout_for_budget(2400) > timeout_for_budget(1200)


def test_the_floor_rate_is_conservative() -> None:
    """A floor above what the box delivers would abort healthy generations."""
    assert MIN_EXPECTED_TOKENS_PER_SECOND < MEASURED_TOKENS_PER_SECOND


def test_overhead_covers_a_turn_that_generates_nothing() -> None:
    """Prompt processing and queueing still have to fit when max_tokens is
    tiny, or a short call fails for want of headroom."""
    assert timeout_for_budget(0) == pytest.approx(RUNTIME_OVERHEAD_SECONDS)
    assert timeout_for_budget(1) > 30


def test_the_advisor_asks_for_a_timeout_that_matches_its_own_budget() -> None:
    """The wiring, not just the helper: chat passes its real budget through, so
    the runtime's patience and the answer's size are one decision, not two."""
    import inspect

    from family_cfo_api.api import chat

    source = inspect.getsource(chat._try_agentic_answer)
    assert "answer_max_tokens=_ANSWER_MAX_TOKENS" in source
    assert chat._ANSWER_MAX_TOKENS == ANSWER_MAX_TOKENS, (
        "the budget these tests reason about moved; re-measure the box's "
        "tokens/second before assuming the derived timeout still covers it"
    )


def test_the_selected_runtime_gets_the_derived_timeout(monkeypatch) -> None:
    """End of the wire: what the adapter is actually constructed with."""
    from family_cfo_api import ai_runtime_selection

    captured: dict = {}

    class FakeAdapter:
        def __init__(self, base_url, model, *, timeout_seconds=None, **_kw):
            captured["timeout"] = timeout_seconds

    class UsableConfig:
        is_usable = True
        base_url = "http://runtime.invalid:8000"
        model = "a-model"

    monkeypatch.setattr(ai_runtime_selection, "VLLMAdapter", FakeAdapter)
    monkeypatch.setattr(
        ai_runtime_selection, "resolve_ai_config", lambda *a, **k: UsableConfig()
    )

    ai_runtime_selection.select_tool_runtime(None, "hh", None, answer_max_tokens=2400)
    assert captured["timeout"] == timeout_for_budget(2400)
    assert captured["timeout"] > 140, "must cover a full-budget answer on this box"
