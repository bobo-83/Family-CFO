from family_cfo_ai_orchestrator import RuntimeCompletion, RuntimeUnavailableError

from family_cfo_api.explanation import PurchaseExplanationContext
from family_cfo_api.llm_explanation import LlmExplanationAdapter
from family_cfo_financial_engine import Money


def _context() -> PurchaseExplanationContext:
    return PurchaseExplanationContext(
        item="a new laptop",
        price=Money(150_000, "USD"),
        net_worth_after=Money(-2_981_500, "USD"),
        emergency_fund_months_before=9.6,
        emergency_fund_months_after=8.9,
        discretionary_months_consumed=0.4,
        warnings=[],
    )


class _StubRuntimeAdapter:
    def __init__(self, completion: RuntimeCompletion | None = None, error: Exception | None = None) -> None:
        self._completion = completion
        self._error = error
        self.calls = 0

    def complete(self, messages, *, temperature: float = 0.2, max_tokens: int = 400) -> RuntimeCompletion:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._completion is not None
        return self._completion


def test_llm_explanation_returns_grounded_text_when_guardrail_passes() -> None:
    runtime = _StubRuntimeAdapter(
        completion=RuntimeCompletion(
            text="Buying a new laptop for USD 1,500.00 moves emergency fund coverage to 8.9 months.",
            model="llama-3-8b-instruct",
            raw={},
        )
    )
    adapter = LlmExplanationAdapter(runtime, model_version="llama-3-8b-instruct")

    result = adapter.explain_purchase(_context())

    assert result.source == "llm"
    assert result.model_version == "llama-3-8b-instruct"
    assert result.prompt_version == "purchase-advisor-v1"
    assert "8.9 months" in result.text
    assert runtime.calls == 1


def test_llm_explanation_falls_back_on_guardrail_violation() -> None:
    runtime = _StubRuntimeAdapter(
        completion=RuntimeCompletion(
            text="This purchase carries a 22.5% hidden risk premium.",
            model="llama-3-8b-instruct",
            raw={},
        )
    )
    adapter = LlmExplanationAdapter(runtime, model_version="llama-3-8b-instruct")

    result = adapter.explain_purchase(_context())

    assert result.source == "deterministic_stub"
    assert result.model_version is None
    assert "a new laptop" in result.text


def test_llm_explanation_falls_back_on_runtime_unavailable() -> None:
    runtime = _StubRuntimeAdapter(error=RuntimeUnavailableError("no route to host"))
    adapter = LlmExplanationAdapter(runtime, model_version="llama-3-8b-instruct")

    result = adapter.explain_purchase(_context())

    assert result.source == "deterministic_stub"
    assert runtime.calls == 1
