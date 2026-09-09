from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class UnreadableAmountSource:
    """Request-local identity for one unreadable stored monetary cell.

    The identity is deliberately internal. Public and persisted representations
    expose only the cardinality of source sets.
    """

    household_id: str
    table: str
    row_id: str
    column: str


SourceSet = frozenset[UnreadableAmountSource]


def union_sources(*values: object) -> SourceSet:
    """Union qualified values or source iterables without ever adding counts."""

    merged: set[UnreadableAmountSource] = set()
    for value in values:
        if value is None:
            continue
        sources = getattr(value, "incomplete_sources", value)
        merged.update(sources)  # type: ignore[arg-type]
    return frozenset(merged)


@dataclass(frozen=True, slots=True)
class Qualified[T]:
    value: T
    incomplete_sources: SourceSet = frozenset()

    @classmethod
    def complete(cls, value: T) -> Qualified[T]:
        return cls(value=value)

    @property
    def incomplete_count(self) -> int:
        return len(self.incomplete_sources)

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_sources

    def map[U](self, transform: Callable[[T], U]) -> Qualified[U]:
        return Qualified(transform(self.value), self.incomplete_sources)


@dataclass(frozen=True, slots=True)
class QualifiedRows[T]:
    rows: tuple[T, ...]
    incomplete_sources: SourceSet = frozenset()

    @property
    def incomplete_count(self) -> int:
        return len(self.incomplete_sources)

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_sources


@dataclass(frozen=True, slots=True)
class AmountCandidate[M]:
    metadata: M
    amount: int | None
    incomplete_source: UnreadableAmountSource | None = None

    def __post_init__(self) -> None:
        if (self.amount is None) == (self.incomplete_source is None):
            raise ValueError("exactly one of amount or incomplete_source is required")

    @property
    def incomplete_sources(self) -> SourceSet:
        return (
            frozenset({self.incomplete_source})
            if self.incomplete_source is not None
            else frozenset()
        )


@dataclass(frozen=True, slots=True)
class CategorySpendingTotals:
    by_category: Mapping[str, Qualified[int]]
    categorized_total: Qualified[int]
    uncategorized: Qualified[int]
    overall: Qualified[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_category", MappingProxyType(dict(self.by_category)))
