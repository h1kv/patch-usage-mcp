"""Pure functions that derive insights from Patch usage data.

These take already-fetched API payloads (plus an explicit `today` for testability)
and return plain dicts. No network, no clock access, so they unit-test cleanly.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any


def _parse_date(value: str) -> date:
    """Parse a date or ISO datetime string into a date (date part only)."""
    return datetime.fromisoformat(value).date()


def enrich_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the usage summary with remaining_usd and percent_used.

    These are the two numbers people actually ask for ("how much is left?",
    "what percent have I used?"), so we compute them once here and reuse.
    """
    enriched = dict(summary)
    limit = summary.get("monthly_limit_usd")
    used = summary.get("current_month_usage_usd")
    if isinstance(limit, (int, float)) and isinstance(used, (int, float)):
        enriched["remaining_usd"] = round(limit - used, 6)
        enriched["percent_used"] = round(used / limit * 100, 2) if limit else None
    return enriched


def burn_rate(
    summary: dict[str, Any],
    daily: list[dict[str, Any]],
    today: date,
    window_days: int = 7,
) -> dict[str, Any]:
    """Project spend from recent daily burn.

    Looks at the most recent `window_days` daily entries to compute an average
    daily spend, then projects month-end spend and a budget-exhaustion date
    against the summary's limit and reset date.
    """
    limit = float(summary.get("monthly_limit_usd") or 0.0)
    used = float(summary.get("current_month_usage_usd") or 0.0)
    remaining = round(limit - used, 6)

    window = [float(d.get("cost") or 0.0) for d in daily[-window_days:]]
    avg_daily = round(sum(window) / len(window), 6) if window else 0.0

    reset_at = summary.get("reset_at")
    reset_date = _parse_date(reset_at) if reset_at else None
    days_until_reset = max((reset_date - today).days, 0) if reset_date else None

    projected_additional = (
        round(avg_daily * days_until_reset, 6) if days_until_reset is not None else None
    )
    projected_month_end_usd = (
        round(used + projected_additional, 6)
        if projected_additional is not None
        else None
    )

    exhaustion_date: str | None = None
    days_to_exhaustion: float | None = None
    exhausts_before_reset: bool | None = None
    if avg_daily > 0 and remaining > 0:
        days_to_exhaustion = round(remaining / avg_daily, 2)
        exhaustion_date = (today + timedelta(days=math.ceil(days_to_exhaustion))).isoformat()
        if reset_date is not None:
            exhausts_before_reset = _parse_date(exhaustion_date) < reset_date
    elif remaining <= 0:
        days_to_exhaustion = 0.0
        exhaustion_date = today.isoformat()
        exhausts_before_reset = True

    return {
        "as_of": today.isoformat(),
        "window_days": len(window),
        "avg_daily_usd": avg_daily,
        "remaining_usd": remaining,
        "monthly_limit_usd": limit,
        "current_month_usage_usd": used,
        "reset_at": reset_at,
        "days_until_reset": days_until_reset,
        "projected_additional_usd": projected_additional,
        "projected_month_end_usd": projected_month_end_usd,
        "projected_overage_usd": (
            round(projected_month_end_usd - limit, 6)
            if projected_month_end_usd is not None and projected_month_end_usd > limit
            else 0.0
        ),
        "days_to_exhaustion": days_to_exhaustion,
        "projected_exhaustion_date": exhaustion_date,
        "exhausts_before_reset": exhausts_before_reset,
    }


def spend_report(
    summary: dict[str, Any],
    daily: list[dict[str, Any]],
    today: date,
    recent_days: int = 7,
) -> dict[str, Any]:
    """One combined, human-friendly spend report.

    Bundles the summary, recent daily spend, a burn-rate projection, and a
    short pre-rendered text summary so a single tool call answers "where do I
    stand?".
    """
    enriched = enrich_summary(summary)
    limit = float(summary.get("monthly_limit_usd") or 0.0)
    used = float(summary.get("current_month_usage_usd") or 0.0)
    percent_used = enriched.get("percent_used")
    burn = burn_rate(summary, daily, today, window_days=recent_days)
    recent = daily[-recent_days:]

    lines = [
        f"Patch spend as of {today.isoformat()}:",
        f"  Used this month: ${used:,.2f} of ${limit:,.2f} "
        + (f"({percent_used:.1f}%)" if percent_used is not None else ""),
        f"  Remaining: ${burn['remaining_usd']:,.2f}",
        f"  Avg/day (last {burn['window_days']}d): ${burn['avg_daily_usd']:,.2f}",
        f"  Resets: {summary.get('reset_at')}",
    ]
    if summary.get("is_throttled"):
        lines.append("  STATUS: THROTTLED")
    if burn["exhausts_before_reset"]:
        lines.append(
            f"  WARNING: at this rate budget runs out ~{burn['projected_exhaustion_date']}, "
            "before the reset."
        )
    elif burn["projected_overage_usd"]:
        lines.append(
            f"  WARNING: projected to exceed limit by ${burn['projected_overage_usd']:,.2f} "
            "by reset."
        )

    return {
        "summary": enriched,
        "recent_daily": recent,
        "burn_rate": burn,
        "report_text": "\n".join(lines),
    }
