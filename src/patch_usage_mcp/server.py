"""MCP server exposing Patch API usage and account tools.

Reads PATCH_TOKEN (required) and PATCH_BASE_URL (optional) from the environment.

Tools are grouped by safety:
  * Read-only (usage, account, tokens, rate-limit requests, analytics)
  * Mutating (create / revoke API tokens), marked destructive AND guarded by an
    explicit `confirm` argument so they never fire accidentally, even if a client
    auto-approves the call.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import analytics
from .client import DEFAULT_BASE_URL, DEFAULT_CLIENT_ID, PatchClient

mcp = FastMCP("patch-usage")

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)


def _client() -> PatchClient:
    return PatchClient(
        token=os.environ.get("PATCH_TOKEN", ""),
        base_url=os.environ.get("PATCH_BASE_URL", DEFAULT_BASE_URL),
        client_id=os.environ.get("PATCH_CLIENT_ID", DEFAULT_CLIENT_ID),
    )


def _today():
    return datetime.now(timezone.utc).date()


def _summary_and_daily(client: PatchClient) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch the two payloads both analytics tools need."""
    return client.get("/user/usage/summary"), client.get("/user/usage/daily")


# --------------------------------------------------------------------------- #
# Read-only: usage & account                                                  #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations=READ_ONLY)
def get_usage_summary() -> dict[str, Any]:
    """Get the headline Patch usage summary for the signed-in account.

    Use this first for "how much have I used?", "how much budget is left?", or
    "am I throttled?". Money values are US dollars. Returns the API fields
    (monthly_limit_usd, current_month_usage_usd, all_time_usage_usd, reset_at,
    is_throttled) plus computed remaining_usd and percent_used.
    """
    return analytics.enrich_summary(_client().get("/user/usage/summary"))


@mcp.tool(annotations=READ_ONLY)
def get_account() -> dict[str, Any]:
    """Get the Patch account profile (id, email, name, created_at, is_admin)."""
    return _client().get("/user/me")


@mcp.tool(annotations=READ_ONLY)
def get_usage_daily() -> list[dict[str, Any]]:
    """Get per-day Patch spend for ~30 days as [{date, cost}], oldest first."""
    return _client().get("/user/usage/daily")


@mcp.tool(annotations=READ_ONLY)
def get_usage_hourly() -> list[dict[str, Any]]:
    """Get per-hour Patch spend for ~24h as [{time, cost}], oldest first."""
    return _client().get("/user/usage/hourly")


# --------------------------------------------------------------------------- #
# Read-only: tokens & rate-limit requests                                     #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations=READ_ONLY)
def list_api_tokens() -> list[dict[str, Any]]:
    """List the account's API keys (read-only).

    Returns [{id, name, prefix, created_at, last_used_at}]. Only the key prefix
    is shown. Full secrets are never retrievable after creation.
    """
    return _client().get("/user/tokens")


@mcp.tool(annotations=READ_ONLY)
def list_rate_limit_requests() -> list[dict[str, Any]]:
    """List the account's budget/rate-limit increase requests (read-only).

    Returns [{id, requested_limit_usd, purpose, status, created_at}].
    """
    return _client().get("/user/rate-limit-requests")


# --------------------------------------------------------------------------- #
# Read-only: analytics (derived from usage data)                              #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations=READ_ONLY)
def get_burn_rate(window_days: int = 7) -> dict[str, Any]:
    """Project spend from recent daily burn.

    Computes average daily spend over the last `window_days`, then projects
    month-end spend and a budget-exhaustion date vs. the reset date. Use for
    "will I run out before reset?" or "am I on track to exceed my limit?".
    """
    summary, daily = _summary_and_daily(_client())
    return analytics.burn_rate(summary, daily, _today(), window_days=window_days)


@mcp.tool(annotations=READ_ONLY)
def get_spend_report(recent_days: int = 7) -> dict[str, Any]:
    """One combined spend report: summary + recent daily + burn-rate projection.

    Returns structured data plus a pre-rendered `report_text`. Use this for an
    at-a-glance "where do I stand?" answer in a single call.
    """
    summary, daily = _summary_and_daily(_client())
    return analytics.spend_report(summary, daily, _today(), recent_days=recent_days)


# --------------------------------------------------------------------------- #
# Mutating: API tokens (destructive + explicit confirm gate)                  #
# --------------------------------------------------------------------------- #


@mcp.tool(annotations=DESTRUCTIVE)
def create_api_token(name: str, confirm: bool = False) -> dict[str, Any]:
    """Create a new Patch API key (sk-svcacct-...). SENSITIVE.

    The full secret is returned exactly once and cannot be retrieved later.
    Guarded: this does nothing unless `confirm=True`. When called with
    confirm=False (the default) it returns a preview and takes no action, so it
    can never mint a key by accident. Always surface the request to the user and
    get their go-ahead before passing confirm=True.
    """
    if not confirm:
        return {
            "status": "confirmation_required",
            "action": "create_api_token",
            "name": name,
            "message": (
                f"This will create a NEW live API key named '{name}'. The secret "
                "is shown only once. Re-call with confirm=true to proceed."
            ),
        }
    created = _client().post("/user/tokens", json={"name": name})
    return {
        "status": "created",
        "warning": "Store this secret now. It cannot be retrieved again.",
        "token": created,
    }


@mcp.tool(annotations=DESTRUCTIVE)
def revoke_api_token(token_id: str, confirm: bool = False) -> dict[str, Any]:
    """Revoke (permanently delete) a Patch API key by its id. DESTRUCTIVE.

    Guarded: with confirm=False (the default) it looks up the key and returns a
    preview of exactly which key would be revoked, taking no action. Only when
    confirm=True does it delete. Use list_api_tokens to find the id; always
    confirm the name/prefix with the user before passing confirm=True.
    """
    client = _client()
    if not confirm:
        tokens = client.get("/user/tokens")
        match = next((t for t in tokens if t.get("id") == token_id), None)
        if match is None:
            return {
                "status": "not_found",
                "token_id": token_id,
                "message": "No API key with that id. Use list_api_tokens to check.",
            }
        return {
            "status": "confirmation_required",
            "action": "revoke_api_token",
            "token": {"id": token_id, "name": match.get("name"), "prefix": match.get("prefix")},
            "message": (
                f"This will PERMANENTLY revoke key '{match.get('name')}' "
                f"({match.get('prefix')}...). Re-call with confirm=true to proceed."
            ),
        }
    client.delete(f"/user/tokens/{token_id}")
    return {"status": "revoked", "token_id": token_id}


def main() -> None:
    """Console-script / module entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
