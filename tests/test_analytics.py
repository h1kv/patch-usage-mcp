"""Unit tests for the analytics module (pure, no network/clock)."""

from __future__ import annotations

from datetime import date

from patch_usage_mcp import analytics

SUMMARY = {
    "monthly_limit_usd": 550.0,
    "current_month_usage_usd": 490.0,
    "all_time_usage_usd": 490.0,
    "reset_at": "2026-07-01T00:00:00",
    "is_throttled": False,
}

# 7 days at $20/day average.
DAILY = [{"date": f"2026-06-2{d}", "cost": 20.0} for d in range(1, 8)]


def test_burn_rate_projects_exhaustion_before_reset():
    out = analytics.burn_rate(SUMMARY, DAILY, today=date(2026, 6, 28))
    assert out["avg_daily_usd"] == 20.0
    assert out["remaining_usd"] == 60.0
    # 60 / 20 = 3 days -> 2026-07-01; reset is 2026-07-01 so 07-01 is NOT before it.
    assert out["days_to_exhaustion"] == 3.0
    assert out["projected_exhaustion_date"] == "2026-07-01"
    assert out["exhausts_before_reset"] is False
    assert out["days_until_reset"] == 3


def test_burn_rate_flags_exhaustion_strictly_before_reset():
    fast = {**SUMMARY, "current_month_usage_usd": 510.0}  # $40 left
    out = analytics.burn_rate(fast, DAILY, today=date(2026, 6, 28))
    # 40 / 20 = 2 days -> 2026-06-30, strictly before 07-01.
    assert out["projected_exhaustion_date"] == "2026-06-30"
    assert out["exhausts_before_reset"] is True


def test_burn_rate_projects_overage():
    out = analytics.burn_rate(SUMMARY, DAILY, today=date(2026, 6, 28))
    # used 490 + 20*3 = 550 -> no overage at exactly the limit.
    assert out["projected_month_end_usd"] == 550.0
    assert out["projected_overage_usd"] == 0.0


def test_burn_rate_zero_spend_has_no_exhaustion():
    quiet = [{"date": "2026-06-27", "cost": 0.0}]
    out = analytics.burn_rate(SUMMARY, quiet, today=date(2026, 6, 28))
    assert out["avg_daily_usd"] == 0.0
    assert out["projected_exhaustion_date"] is None
    assert out["exhausts_before_reset"] is None


def test_burn_rate_already_exhausted():
    spent = {**SUMMARY, "current_month_usage_usd": 550.0}  # $0 left
    out = analytics.burn_rate(spent, DAILY, today=date(2026, 6, 28))
    assert out["remaining_usd"] == 0.0
    assert out["days_to_exhaustion"] == 0.0
    assert out["exhausts_before_reset"] is True


def test_spend_report_bundles_and_renders_text():
    out = analytics.spend_report(SUMMARY, DAILY, today=date(2026, 6, 28))
    assert out["summary"]["percent_used"] == round(490 / 550 * 100, 2)
    assert out["recent_daily"] == DAILY[-7:]
    assert "Patch spend as of 2026-06-28" in out["report_text"]
    assert out["burn_rate"]["avg_daily_usd"] == 20.0


def test_spend_report_warns_when_throttled():
    throttled = {**SUMMARY, "is_throttled": True}
    out = analytics.spend_report(throttled, DAILY, today=date(2026, 6, 28))
    assert "THROTTLED" in out["report_text"]
