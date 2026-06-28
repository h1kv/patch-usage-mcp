"""HTTP client for the Patch API (https://oai.joinpatch.org/api).

The dashboard authenticates with a JWT stored in browser localStorage as
`patch_token`. This client takes that same token and replays the dashboard's
own authenticated GET requests. Every failure is mapped to a message that is
safe and useful to show the user.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from . import __version__

DEFAULT_BASE_URL = "https://oai.joinpatch.org/api"
TIMEOUT_SECONDS = 25.0  # mirrors the dashboard's own client timeout

# Identifies MCP-originated requests so the API's logs can separate them from
# human (browser/dashboard) traffic. The X-Patch-Client value is configurable
# via PATCH_CLIENT_ID (set it empty to drop the custom header entirely).
CLIENT_NAME = "patch-usage-mcp"
CLIENT_HEADER = "X-Patch-Client"
DEFAULT_CLIENT_ID = CLIENT_NAME

# Shown whenever the token is missing/expired/rejected, so the user always
# knows exactly how to recover.
REAUTH_HINT = (
    "Re-copy your token: open https://oai.joinpatch.org while signed in, open "
    "DevTools (F12) -> Console, run  localStorage.getItem('patch_token')  and "
    "set the PATCH_TOKEN env var in your MCP config to that value."
)


def _client_headers(client_id: str) -> dict[str, str]:
    """Build the identifying headers sent on every request.

    Always sets a descriptive User-Agent; adds the X-Patch-Client header unless
    client_id is empty (the opt-out).
    """
    headers = {"User-Agent": f"{CLIENT_NAME}/{__version__}"}
    if client_id:
        headers[CLIENT_HEADER] = client_id
    return headers


class PatchError(RuntimeError):
    """A Patch API call failed. The message is safe to surface to the user."""


def decode_exp(token: str) -> float | None:
    """Return the JWT `exp` claim (epoch seconds) if decodable, else None."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


class PatchClient:
    """Thin authenticated GET client for the Patch API."""

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        client_id: str = DEFAULT_CLIENT_ID,
    ) -> None:
        if not token:
            raise PatchError("PATCH_TOKEN is not set. " + REAUTH_HINT)
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._id_headers = _client_headers(client_id)

    def _check_not_expired(self) -> None:
        exp = decode_exp(self._token)
        if exp is not None and exp <= time.time():
            raise PatchError("Your Patch token has expired. " + REAUTH_HINT)

    def get(self, path: str) -> Any:
        """GET `{base_url}{path}` with the bearer token; return parsed JSON."""
        return self._request("GET", path)

    def post(self, path: str, json: Any | None = None) -> Any:
        """POST `{base_url}{path}`; return parsed JSON."""
        return self._request("POST", path, json=json)

    def delete(self, path: str) -> Any:
        """DELETE `{base_url}{path}`; return parsed JSON (or None on 204)."""
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, json: Any | None = None) -> Any:
        self._check_not_expired()
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}", **self._id_headers}
        try:
            resp = httpx.request(
                method, url, headers=headers, json=json, timeout=TIMEOUT_SECONDS
            )
        except httpx.TimeoutException as exc:
            raise PatchError(
                f"Patch backend timed out after {TIMEOUT_SECONDS:.0f}s ({url})."
            ) from exc
        except httpx.RequestError as exc:
            raise PatchError(
                f"Could not reach the Patch backend at {url}: {exc}"
            ) from exc

        if resp.status_code == 401:
            raise PatchError("Patch rejected the token (401). " + REAUTH_HINT)
        if resp.status_code >= 400:
            raise PatchError(
                f"Patch API error (HTTP {resp.status_code}): {_extract_detail(resp)}"
            )
        if resp.status_code == 204:
            return None
        return resp.json()


def _extract_detail(resp: httpx.Response) -> str:
    """Pull the API's `detail` field (its error contract), else fall back."""
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except Exception:
        pass
    return resp.text or resp.reason_phrase
