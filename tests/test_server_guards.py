"""Tests for the confirm-gate on mutating tools and annotation correctness."""

from __future__ import annotations

import asyncio

import pytest

from patch_usage_mcp import server


class FakeClient:
    """Records calls so we can assert no mutation happens without confirm."""

    def __init__(self, tokens=None):
        self._tokens = tokens or []
        self.posted = []
        self.deleted = []

    def get(self, path):
        if path == "/user/tokens":
            return self._tokens
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, json=None):
        self.posted.append((path, json))
        return {"id": "new-id", "name": json["name"], "secret": "sk-svcacct-SECRET"}

    def delete(self, path):
        self.deleted.append(path)
        return None


def test_create_token_requires_confirm(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    out = server.create_api_token("my-key")  # confirm defaults False
    assert out["status"] == "confirmation_required"
    assert fake.posted == []  # nothing created


def test_create_token_with_confirm_creates(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    out = server.create_api_token("my-key", confirm=True)
    assert out["status"] == "created"
    assert fake.posted == [("/user/tokens", {"name": "my-key"})]
    assert out["token"]["secret"].startswith("sk-svcacct-")


def test_revoke_token_requires_confirm_and_previews(monkeypatch):
    fake = FakeClient(tokens=[{"id": "abc", "name": "k1", "prefix": "sk-svcacct-AAA"}])
    monkeypatch.setattr(server, "_client", lambda: fake)
    out = server.revoke_api_token("abc")  # confirm defaults False
    assert out["status"] == "confirmation_required"
    assert out["token"]["name"] == "k1"
    assert fake.deleted == []  # nothing deleted


def test_revoke_unknown_id_is_safe(monkeypatch):
    fake = FakeClient(tokens=[{"id": "abc", "name": "k1", "prefix": "p"}])
    monkeypatch.setattr(server, "_client", lambda: fake)
    out = server.revoke_api_token("does-not-exist")
    assert out["status"] == "not_found"
    assert fake.deleted == []


def test_revoke_token_with_confirm_deletes(monkeypatch):
    fake = FakeClient(tokens=[{"id": "abc", "name": "k1", "prefix": "p"}])
    monkeypatch.setattr(server, "_client", lambda: fake)
    out = server.revoke_api_token("abc", confirm=True)
    assert out["status"] == "revoked"
    assert fake.deleted == ["/user/tokens/abc"]


def test_mutating_tools_annotated_destructive():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ("create_api_token", "revoke_api_token"):
        ann = tools[name].annotations
        assert ann is not None and ann.destructiveHint is True
        assert ann.readOnlyHint is False


def test_read_tools_annotated_readonly():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ("get_usage_summary", "list_api_tokens", "get_burn_rate"):
        assert tools[name].annotations.readOnlyHint is True
