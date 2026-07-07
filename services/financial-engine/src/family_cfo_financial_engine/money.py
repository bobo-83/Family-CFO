from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class CurrencyMismatchError(ValueError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"currency mismatch: {left} vs {right}")
        self.left = left
        self.right = right


@dataclass(frozen=True, slots=True)
class Money:
    """An exact money amount stored as integer minor units plus an ISO 4217 currency code.

    Never backed by float; scaling uses Decimal internally and always rounds
    back to an integer minor-unit amount so no fractional minor units persist.
    """

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise TypeError("amount_minor must be an int")
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError(f"invalid ISO 4217 currency code: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def _check_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("Money can only be multiplied by an int scalar")
        return Money(self.amount_minor * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount_minor >= other.amount_minor

    def is_negative(self) -> bool:
        return self.amount_minor < 0

    def is_zero(self) -> bool:
        return self.amount_minor == 0

    def scale(self, numerator: int, denominator: int) -> Money:
        """Scale by numerator/denominator, rounding half up to the nearest minor unit."""
        if denominator == 0:
            raise ZeroDivisionError("denominator must not be zero")

        exact = (Decimal(self.amount_minor) * Decimal(numerator)) / Decimal(denominator)
        rounded = exact.to_integral_value(rounding=ROUND_HALF_UP)
        return Money(int(rounded), self.currency)

    def ratio(self, other: Money) -> float:
        """Return self / other as a dimensionless float ratio, e.g. for progress percentages."""
        self._check_currency(other)
        if other.amount_minor == 0:
            raise ZeroDivisionError("cannot divide by a zero Money amount")

        return self.amount_minor / other.amount_minor

    def to_dict(self) -> dict[str, int | str]:
        return {"amount_minor": self.amount_minor, "currency": self.currency}
