"""Grant-based RSU tracking (M-rsu-grants).

An earner enters each grant — units of one ticker, grant date, how many years
it vests over, and the cadence — and the app derives the vest schedule as
editable rows. A cached live quote values upcoming vests, and that valuation
REPLACES the flat ``rsu_annual_minor`` figure everywhere it feeds (taxes, the
W-2 baseline, expected income events, the sell-by runway) via
``effective_income_profiles``: consumers swap one loader call and the rest of
the pipeline is untouched. Households without grants (or without a quote yet)
keep the manual figure — grants only ever improve fidelity, never break it.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.finance_service import add_months

VESTS_PER_YEAR = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}

# Free, keyless JSON — deterministic to parse (ADR 0003 spirit: the fetch is
# live data, but everything derived from it is explainable arithmetic).
_QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
_QUOTE_TIMEOUT_SECONDS = 6.0


def derive_vest_schedule(
    units: int, grant_date: date, vest_years: int, frequency: str
) -> list[tuple[date, int]]:
    """The default vest schedule a grant implies: equal tranches at the grant's
    cadence, starting one period after the grant date. Whole shares only; the
    remainder vests one extra share at a time across the EARLIEST tranches
    (matching how plans front-load rounding). The user edits rows after."""
    per_year = VESTS_PER_YEAR.get(frequency)
    if per_year is None or units <= 0 or vest_years <= 0:
        return []
    count = per_year * vest_years
    step_months = 12 // per_year
    base, remainder = divmod(units, count)
    return [
        (add_months(grant_date, step_months * (i + 1)), base + (1 if i < remainder else 0))
        for i in range(count)
        if base + (1 if i < remainder else 0) > 0
    ]


@dataclass(frozen=True, slots=True)
class RsuValuation:
    """Everything grant-aware consumers need, loaded once per request."""

    grants: list[repository.RsuGrantRecord]
    events: list[repository.RsuVestEventRecord]
    quotes: dict[str, repository.StockQuoteRecord]  # by ticker

    def grant(self, grant_id: str) -> repository.RsuGrantRecord | None:
        return next((g for g in self.grants if g.id == grant_id), None)

    def events_for_profile(self, profile_id: str) -> list[repository.RsuVestEventRecord]:
        grant_ids = {g.id for g in self.grants if g.income_profile_id == profile_id}
        return [e for e in self.events if e.grant_id in grant_ids]

    def _price_minor(self, grant_id: str) -> int | None:
        grant = self.grant(grant_id)
        quote = self.quotes.get(grant.ticker) if grant else None
        return quote.price_minor if quote else None

    def upcoming_annual_minor(self, profile_id: str, *, today: date) -> int | None:
        """The next 12 months of vests valued at the cached quote, or None when
        the profile has no grants or its ticker has no quote yet."""
        events = self.events_for_profile(profile_id)
        if not events:
            return None
        # Inclusive horizon: a grant entered today vests at +3/+6/+9/+12 months —
        # the 12-month tranche IS part of "the next year" of vests.
        horizon = add_months(today, 12)
        total = 0
        for event in events:
            if not (today <= event.vest_date <= horizon):
                continue
            price = self._price_minor(event.grant_id)
            if price is None:
                return None
            total += event.units * price
        return total

    def upcoming_events(
        self, profile_id: str, *, today: date, limit: int = 2
    ) -> list[tuple[date, int, int]]:
        """(vest_date, units, value_minor) for the next vests, quote-valued."""
        out: list[tuple[date, int, int]] = []
        for event in self.events_for_profile(profile_id):
            if event.vest_date < today:
                continue
            price = self._price_minor(event.grant_id)
            if price is None:
                continue
            out.append((event.vest_date, event.units, event.units * price))
            if len(out) >= limit:
                break
        return out


def load_valuation(engine: Engine, household_id: str) -> RsuValuation:
    return RsuValuation(
        grants=repository.list_rsu_grants(engine, household_id),
        events=repository.list_rsu_vest_events(engine, household_id),
        quotes={q.ticker: q for q in repository.list_stock_quotes(engine, household_id)},
    )


def effective_income_profiles(
    engine: Engine, household_id: str, *, today: date | None = None
) -> list[repository.IncomeProfileRecord]:
    """Income profiles with grant-derived RSU figures substituted in.

    For an earner with grants AND a cached quote: rsu_annual_minor becomes the
    next 12 months of vests at the live price, rsu_next_vest_date the next
    event, rsu_frequency the newest grant's cadence. Otherwise the record is
    returned untouched (the manual figure stays authoritative).
    """
    profiles = repository.list_income_profiles(engine, household_id)
    valuation = load_valuation(engine, household_id)
    if not valuation.grants:
        return profiles
    today = today or date.today()
    effective: list[repository.IncomeProfileRecord] = []
    for profile in profiles:
        derived = valuation.upcoming_annual_minor(profile.id, today=today)
        if derived is None:
            effective.append(profile)
            continue
        events = [e for e in valuation.events_for_profile(profile.id) if e.vest_date >= today]
        newest_grant = next(
            (g for g in valuation.grants if g.income_profile_id == profile.id), None
        )
        effective.append(
            replace(
                profile,
                rsu_annual_minor=derived,
                rsu_next_vest_date=events[0].vest_date if events else None,
                rsu_frequency=newest_grant.frequency if newest_grant else profile.rsu_frequency,
            )
        )
    return effective


def fetch_quote(ticker: str, *, settings: Settings | None = None) -> tuple[int, str] | None:
    """(price_minor, source) from the live market, or None when disabled or
    unreachable. Never raises — a quote is an enhancement, not a dependency."""
    settings = settings or get_settings()
    if not settings.live_data_enabled:
        return None
    symbol = urllib.parse.quote(ticker.upper())
    request = urllib.request.Request(
        _QUOTE_URL.format(symbol=symbol),
        # Yahoo rejects the default urllib user agent.
        headers={"User-Agent": "Mozilla/5.0 (FamilyCFO self-hosted)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_QUOTE_TIMEOUT_SECONDS) as response:
            data = json.load(response)
        meta = data["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        if price <= 0:
            return None
        return int(round(price * 100)), "yahoo-chart"
    except Exception:  # noqa: BLE001 — any failure means "no quote right now"
        return None


def refresh_quotes(
    engine: Engine, household_id: str, *, settings: Settings | None = None
) -> list[repository.StockQuoteRecord]:
    """Best-effort refresh of every ticker the household holds grants in.
    Returns the (possibly stale) cached quotes afterward."""
    tickers = {g.ticker for g in repository.list_rsu_grants(engine, household_id)}
    for ticker in sorted(tickers):
        fetched = fetch_quote(ticker, settings=settings)
        if fetched is None:
            continue
        price_minor, source = fetched
        repository.upsert_stock_quote(
            engine,
            household_id,
            ticker,
            price_minor=price_minor,
            currency="USD",
            as_of=datetime.now(UTC),
            source=source,
        )
    return repository.list_stock_quotes(engine, household_id)
