"""Token and cost accounting shared by model-backed pilot runs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .llm.base import ModelUsage


SONNET_STANDARD_PRICING_DATE = date(2026, 8, 20)
SONNET_STANDARD_PRICING_URL = (
    "https://platform.claude.com/docs/en/about-claude/pricing"
)


class SonnetStandardRates(BaseModel):
    """USD rates per million tokens for the selected Sonnet tier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pricing_date: date = SONNET_STANDARD_PRICING_DATE
    official_source_url: str = SONNET_STANDARD_PRICING_URL
    input_usd_per_million: float = 2.0
    output_usd_per_million: float = 10.0
    cache_write_5m_usd_per_million: float = 2.5
    cache_read_usd_per_million: float = 0.2


SONNET_STANDARD_RATES = SonnetStandardRates()


class UsageCostSummary(BaseModel):
    """Summed usage and its cost under one explicitly dated rate table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_write_5m_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    input_cost_usd: float = Field(ge=0)
    output_cost_usd: float = Field(ge=0)
    cache_write_5m_cost_usd: float = Field(ge=0)
    cache_read_cost_usd: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    rates: SonnetStandardRates


def _token_count(value: int | None, field_name: str) -> int:
    if value is None:
        return 0
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _cost(tokens: int, usd_per_million: float) -> Decimal:
    return Decimal(tokens) * Decimal(str(usd_per_million)) / Decimal(1_000_000)


def summarize_usage(
    usage_items: Iterable[ModelUsage],
    *,
    rates: SonnetStandardRates = SONNET_STANDARD_RATES,
) -> UsageCostSummary:
    """Sum provider counters without estimating tokens that were not reported."""

    items = list(usage_items)
    input_tokens = sum(
        _token_count(item.input_tokens, "input_tokens") for item in items
    )
    output_tokens = sum(
        _token_count(item.output_tokens, "output_tokens") for item in items
    )
    cache_write_tokens = sum(
        _token_count(
            getattr(item, "cache_creation_input_tokens", None),
            "cache_creation_input_tokens",
        )
        for item in items
    )
    cache_read_tokens = sum(
        _token_count(
            getattr(item, "cache_read_input_tokens", None),
            "cache_read_input_tokens",
        )
        for item in items
    )

    input_cost = _cost(input_tokens, rates.input_usd_per_million)
    output_cost = _cost(output_tokens, rates.output_usd_per_million)
    cache_write_cost = _cost(
        cache_write_tokens,
        rates.cache_write_5m_usd_per_million,
    )
    cache_read_cost = _cost(cache_read_tokens, rates.cache_read_usd_per_million)
    total_cost = input_cost + output_cost + cache_write_cost + cache_read_cost

    return UsageCostSummary(
        call_count=len(items),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_5m_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
        input_cost_usd=float(input_cost),
        output_cost_usd=float(output_cost),
        cache_write_5m_cost_usd=float(cache_write_cost),
        cache_read_cost_usd=float(cache_read_cost),
        total_cost_usd=float(total_cost),
        rates=rates,
    )
