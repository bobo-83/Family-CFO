from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CALCULATION_ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class CalculationResult:
    """The auditable contract every financial engine calculation returns.

    ADR 0003 requires calculation outputs to carry inputs, assumptions,
    version, and warnings so recommendation responses can cite them.
    """

    calculation_type: str
    version: str
    inputs: dict[str, Any]
    assumptions: list[str]
    outputs: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
