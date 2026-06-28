"""Unit tests for PatchClient: JWT-expiry handling and error mapping.

These mock httpx so they run without a live token or network access.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from patch_usage_mcp import client as client_mod
from patch_usage_mcp.client import PatchClient, PatchError, decode_exp


def make_token(exp: float | None) -> str:
    """Build a syntactically valid JWT with the given exp claim (no signature)."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload_obj = {"sub": "x"}
    if exp is not None:
        payload_obj["exp"] = exp
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_decode_exp_roundtrip():
    assert decode_exp(make_token(1783102924)) == 1783102924.0


def test_decode_exp_garbage_returns_none():
    assert decode_exp("not-a-jwt") is None


def test_missing_token_raises():
    with pytest.raises(PatchError, match="PATCH_TOKEN is not set"):
        PatchClient(token="")


def test_expired_token_raises_before_network(monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("network should not be called for an expired token")

    monkeypatch.setattr(client_mod.httpx, "request", boom)
    c = PatchClient(token=make_token(time.time() - 10))
    with pytest.raises(PatchError, match="expired"):
        c.get("/user/usage/summary")


def _fake_request(response=None, exc=None):
    def _req(method, url, **kwargs):
        if exc is not None:
            raise exc
        return response
    return _req


def test_success_returns_parsed_json(monkeypatch):
    resp = httpx.Response(200, json={"current_month_usage_usd": 1.5})
    monkeypatch.setattr(client_mod.httpx, "request", _fake_request(response=resp))
    c = PatchClient(token=make_token(time.time() + 3600))
    assert c.get("/user/usage/summary") == {"current_month_usage_usd": 1.5}


def test_401_maps_to_reauth_hint(monkeypatch):
    resp = httpx.Response(401, json={"detail": "expired"})
    monkeypatch.setattr(client_mod.httpx, "request", _fake_request(response=resp))
    c = PatchClient(token=make_token(time.time() + 3600))
    with pytest.raises(PatchError, match="patch_token"):
        c.get("/user/me")


def test_other_error_surfaces_detail(monkeypatch):
    resp = httpx.Response(429, json={"detail": "slow down"})
    monkeypatch.setattr(client_mod.httpx, "request", _fake_request(response=resp))
    c = PatchClient(token=make_token(time.time() + 3600))
    with pytest.raises(PatchError, match="slow down"):
        c.get("/user/usage/daily")


def test_timeout_maps_to_friendly_message(monkeypatch):
    monkeypatch.setattr(
        client_mod.httpx, "request", _fake_request(exc=httpx.TimeoutException("t"))
    )
    c = PatchClient(token=make_token(time.time() + 3600))
    with pytest.raises(PatchError, match="timed out"):
        c.get("/user/usage/hourly")


def test_connect_error_maps_to_friendly_message(monkeypatch):
    monkeypatch.setattr(
        client_mod.httpx, "request", _fake_request(exc=httpx.ConnectError("refused"))
    )
    c = PatchClient(token=make_token(time.time() + 3600))
    with pytest.raises(PatchError, match="Could not reach"):
        c.get("/user/me")


def test_delete_204_returns_none(monkeypatch):
    resp = httpx.Response(204)
    monkeypatch.setattr(client_mod.httpx, "request", _fake_request(response=resp))
    c = PatchClient(token=make_token(time.time() + 3600))
    assert c.delete("/user/tokens/abc") is None
