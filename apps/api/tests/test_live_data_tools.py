import httpx
from sqlalchemy.engine import Engine

from family_cfo_api import ai_tools, fixtures
from family_cfo_api.config import Settings

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _executor(engine: Engine, **settings_overrides):
    settings = Settings(**settings_overrides)
    return ai_tools.build_executor(engine, _HH, "USD", settings)


def test_exchange_rate_tool_registered_by_default_and_search_only_when_configured() -> None:
    default_names = {t.name for t in ai_tools.build_tools(Settings())}
    assert "get_exchange_rate" in default_names
    assert "web_search" not in default_names

    with_search = {t.name for t in ai_tools.build_tools(Settings(searxng_url="http://searxng:8080"))}
    assert "web_search" in with_search

    disabled = {t.name for t in ai_tools.build_tools(Settings(live_data_enabled=False))}
    assert "get_exchange_rate" not in disabled


def test_exchange_rate_happy_path(demo_engine: Engine, monkeypatch) -> None:
    def fake_get(url, timeout=None, params=None):
        assert "open.er-api.com" in url and url.endswith("/USD")
        return httpx.Response(
            200,
            json={"rates": {"VND": 25400.5}, "time_last_update_utc": "Wed, 08 Jul 2026"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_tools.httpx, "get", fake_get)
    result = _executor(demo_engine)("get_exchange_rate", {"base": "usd", "quote": "vnd"})

    assert result["rate"] == 25400.5
    assert result["base"] == "USD" and result["quote"] == "VND"
    assert "source" in result


def test_exchange_rate_validates_codes_and_degrades(demo_engine: Engine, monkeypatch) -> None:
    execute = _executor(demo_engine)
    assert execute("get_exchange_rate", {"base": "USDX", "quote": "VND"})["error"] == "invalid_arguments"
    assert execute("get_exchange_rate", {"quote": "VND"})["error"] == "missing_input"

    def down(url, timeout=None, params=None):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(ai_tools.httpx, "get", down)
    assert execute("get_exchange_rate", {"base": "USD", "quote": "VND"})["error"] == "lookup_failed"


def test_exchange_rate_unknown_tool_when_disabled(demo_engine: Engine) -> None:
    execute = _executor(demo_engine, live_data_enabled=False)
    assert execute("get_exchange_rate", {"base": "USD", "quote": "VND"})["error"] == "unknown_tool"


def test_web_search_shapes_results_and_bounds_query(demo_engine: Engine, monkeypatch) -> None:
    def fake_get(url, params=None, timeout=None):
        assert url.startswith("http://searxng:8080/search")
        assert params["q"] == "iPhone 16 price"
        return httpx.Response(
            200,
            json={"results": [{"title": "iPhone 16", "content": "From $799", "url": "https://x"}] * 8},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_tools.httpx, "get", fake_get)
    execute = _executor(demo_engine, searxng_url="http://searxng:8080")
    result = execute("web_search", {"query": "iPhone 16 price"})

    assert len(result["results"]) == 5
    assert result["results"][0]["snippet"] == "From $799"

    too_long = execute("web_search", {"query": "x" * 300})
    assert too_long["error"] == "invalid_arguments"


def test_exchange_rate_converts_amount_deterministically(demo_engine: Engine, monkeypatch) -> None:
    def fake_get(url, timeout=None, params=None):
        return httpx.Response(
            200,
            json={"rates": {"VND": 26221.66}, "time_last_update_utc": "Thu, 09 Jul 2026"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_tools.httpx, "get", fake_get)
    result = _executor(demo_engine)(
        "get_exchange_rate", {"base": "USD", "quote": "VND", "amount_minor": 200_000}
    )

    # 200,000 cents * 26221.66 = 5,244,332,000 (minor units of VND) — Decimal math, not the model's.
    assert result["converted"]["amount_minor"] == 5_244_332_000
    assert result["converted"]["currency"] == "VND"
    assert result["amount"]["amount_minor"] == 200_000
