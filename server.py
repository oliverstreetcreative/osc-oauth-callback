"""Oliver Street Creative — Kroger MCP server ("OSC - Kroger Grocery Assistant").

Lets Claude search Kroger products/locations and add items to Sam's real Kroger
cart. Checkout/payment still has to happen on kroger.com or the Kroger app —
Kroger's public developer API has no endpoint for that at this tier.

Repo/URL history: this repo used to be a 30-line stub that just emailed Sam the
raw OAuth code to paste into a tool that never got built. Rewritten 2026-07-04
into a real server, keeping the same repo/Railway service so the redirect_uri
already registered with Kroger (https://osc-oauth-callback-production.up.
railway.app/kroger/callback) didn't need to change.

Auth model (two independent layers):
- Front door (protects the MCP tools from the public internet): a single
  static bearer token, checked via a TokenVerifier subclass. Simpler than the
  GitHub OAuth allowlist used by the OSC Fastmail proxy since this doesn't
  need per-human identity — just needs to not be wide open to the internet.
  Set MCP_BEARER_TOKEN and configure the same value wherever this server is
  added as a connector.
- Kroger auth: standard OAuth2. product.compact/profile.compact via
  client_credentials (app-only, no user needed) covers search/locations.
  cart.basic:write requires a user-linked token, obtained once via a real
  browser consent (authorization_code grant) — Sam visits the URL from
  kroger_get_authorize_url(), logs in, Kroger redirects to /kroger/callback
  here, which exchanges the code for an access+refresh token pair.

Persistence: deliberately simple for a single-user personal tool. The
refresh_token lives in an in-process variable, lost on restart unless
persisted as the KROGER_REFRESH_TOKEN env var (see README for the one-time
bootstrap: authorize -> GET /admin/refresh-token -> `railway variable set`).
No auto-persistence-on-rotation built in — if Kroger ever rotates the token
out from under us, the fallback is just redoing the ~30s browser consent.

Env:
  KROGER_CLIENT_ID       — from the Kroger Developer app.
  KROGER_CLIENT_SECRET   — from the Kroger Developer app (refresh in their
                           dashboard if rotated).
  KROGER_REDIRECT_URI    — must exactly match what's registered with Kroger.
  KROGER_REFRESH_TOKEN   — optional at boot. If set, cart tools work
                           immediately; if not, kroger_get_authorize_url
                           explains how to bootstrap it.
  MCP_BEARER_TOKEN       — static bearer token required to call any MCP tool.
  ADMIN_TOKEN            — separate static token required to hit
                           /admin/refresh-token (see README bootstrap flow).
  PORT                   — injected by Railway.
"""

import base64
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("osc.kroger")

KROGER_CLIENT_ID = os.environ["KROGER_CLIENT_ID"]
KROGER_CLIENT_SECRET = os.environ["KROGER_CLIENT_SECRET"]
KROGER_REDIRECT_URI = os.environ["KROGER_REDIRECT_URI"]
MCP_BEARER_TOKEN = os.environ["MCP_BEARER_TOKEN"]
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]

KROGER_BASE = "https://api.kroger.com/v1"
TOKEN_URL = f"{KROGER_BASE}/connect/oauth2/token"
AUTHORIZE_URL = f"{KROGER_BASE}/connect/oauth2/authorize"

# Mutable in-process state — see module docstring re: persistence tradeoffs.
_state = {
    "refresh_token": os.environ.get("KROGER_REFRESH_TOKEN"),
    "app_access_token": None,
    "app_token_expires_at": 0.0,
    "user_access_token": None,
    "user_token_expires_at": 0.0,
    "pending_oauth_state": None,
}


class StaticBearerVerifier(TokenVerifier):
    """Single shared-secret bearer token — proportionate for a personal
    single-user tool. See the Fastmail proxy for the GitHub-OAuth-allowlist
    pattern used where per-human identity actually matters."""

    def __init__(self, expected_token: str, **kwargs):
        super().__init__(**kwargs)
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if secrets.compare_digest(token, self._expected):
            return AccessToken(token=token, client_id="osc", scopes=[])
        return None


mcp = FastMCP(
    name="OSC - Kroger Grocery Assistant",
    auth=StaticBearerVerifier(MCP_BEARER_TOKEN),
)


def _basic_auth_header() -> str:
    raw = f"{KROGER_CLIENT_ID}:{KROGER_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _get_app_token() -> str:
    """client_credentials token for public scopes (product search, locations)."""
    if _state["app_access_token"] and time.time() < _state["app_token_expires_at"] - 60:
        return _state["app_access_token"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"Authorization": _basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "product.compact"},
        )
        resp.raise_for_status()
        data = resp.json()
    _state["app_access_token"] = data["access_token"]
    _state["app_token_expires_at"] = time.time() + data["expires_in"]
    return _state["app_access_token"]


async def _get_user_token() -> str:
    """authorization_code-derived token for cart writes. Requires the one-time
    browser consent to have already happened (see kroger_get_authorize_url)."""
    if _state["user_access_token"] and time.time() < _state["user_token_expires_at"] - 60:
        return _state["user_access_token"]
    if not _state["refresh_token"]:
        raise RuntimeError(
            "No Kroger user authorization yet. Call kroger_get_authorize_url, "
            "have Sam open it and log in, then retry."
        )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"Authorization": _basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": _state["refresh_token"]},
        )
        resp.raise_for_status()
        data = resp.json()
    _state["user_access_token"] = data["access_token"]
    _state["user_token_expires_at"] = time.time() + data["expires_in"]
    if data.get("refresh_token"):
        _state["refresh_token"] = data["refresh_token"]  # some providers rotate on use
    return _state["user_access_token"]


@mcp.tool
async def kroger_get_authorize_url() -> str:
    """Get the URL Sam needs to open in a browser and log into Kroger with, to
    grant this app cart-write access. One-time (or occasional, if the refresh
    token is ever lost) — after completing it, cart tools work without
    repeating this. Do NOT open this URL yourself; only Sam can complete the
    login, it's his account."""
    state = secrets.token_urlsafe(16)
    _state["pending_oauth_state"] = state
    params = {
        "scope": "cart.basic:write profile.compact",
        "client_id": KROGER_CLIENT_ID,
        "redirect_uri": KROGER_REDIRECT_URI,
        "state": state,
        "response_type": "code",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@mcp.tool
async def kroger_list_locations(zip_code: str, limit: int = 5) -> list[dict]:
    """List nearby Kroger-family store locations for a zip code. Returns
    locationId (needed for product search/cart), name, and address."""
    token = await _get_app_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KROGER_BASE}/locations",
            headers={"Authorization": f"Bearer {token}"},
            params={"filter.zipCode.near": zip_code, "filter.limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "locationId": loc["locationId"],
            "name": loc["name"],
            "address": (
                f"{loc['address']['addressLine1']}, {loc['address']['city']}, "
                f"{loc['address']['state']} {loc['address']['zipCode']}"
            ),
        }
        for loc in data.get("data", [])
    ]


@mcp.tool
async def kroger_search_products(term: str, location_id: str, limit: int = 5) -> list[dict]:
    """Search products at a specific store (get location_id from
    kroger_list_locations first). Returns description, upc (needed for
    kroger_add_to_cart), price, and size."""
    token = await _get_app_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{KROGER_BASE}/products",
            headers={"Authorization": f"Bearer {token}"},
            params={"filter.term": term, "filter.locationId": location_id, "filter.limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
    results = []
    for p in data.get("data", []):
        item = (p.get("items") or [{}])[0]
        price = item.get("price") or {}
        results.append(
            {
                "description": p.get("description"),
                "upc": p.get("upc"),
                "size": item.get("size"),
                "price": price.get("regular"),
                "promoPrice": price.get("promo"),
            }
        )
    return results


@mcp.tool
async def kroger_add_to_cart(items: list[dict]) -> str:
    """Add items to Sam's real Kroger cart. items: list of {"upc": str,
    "quantity": int}. Requires prior authorization (see
    kroger_get_authorize_url) — raises clearly if that hasn't happened yet.
    This adds to the cart only; Sam still has to check out on kroger.com or
    the Kroger app — the public API has no checkout/payment endpoint."""
    token = await _get_user_token()
    payload = {"items": [{"upc": i["upc"], "quantity": i.get("quantity", 1)} for i in items]}
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{KROGER_BASE}/cart/add",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
    return f"Added {len(items)} item(s) to the Kroger cart. Check out at kroger.com or the Kroger app."


@mcp.custom_route("/kroger/callback", methods=["GET"])
async def kroger_callback(request: Request):
    """Kroger redirects here after Sam logs in and consents. Public route —
    Kroger's server can't do our bearer-token auth, so this is deliberately
    outside the MCP-protected surface. Protected instead by the OAuth `state`
    round-trip (CSRF check) and by the fact that a valid `code` can only come
    from Kroger after a real login."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<body>No authorization code received.</body>", status_code=400)
    if state != _state.get("pending_oauth_state"):
        return HTMLResponse("<body>State mismatch — possible CSRF, aborting.</body>", status_code=400)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"Authorization": _basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": KROGER_REDIRECT_URI},
        )
    if resp.status_code != 200:
        logger.error("Kroger token exchange failed: %s", resp.text)
        return HTMLResponse("<body>Token exchange failed — check server logs.</body>", status_code=502)

    data = resp.json()
    _state["refresh_token"] = data["refresh_token"]
    _state["user_access_token"] = data["access_token"]
    _state["user_token_expires_at"] = time.time() + data["expires_in"]
    _state["pending_oauth_state"] = None
    logger.info("Kroger authorization successful — refresh token acquired (value not logged).")
    return HTMLResponse("<body><h2>Kroger connected.</h2>You can close this tab.</body>")


@mcp.custom_route("/admin/refresh-token", methods=["GET"])
async def admin_get_refresh_token(request: Request):
    """One-time retrieval endpoint so the refresh_token can be persisted as a
    durable Railway env var after the bootstrap browser consent, without ever
    putting it in the Kroger redirect response or in logs. Requires
    ADMIN_TOKEN as a bearer header — a separate secret from MCP_BEARER_TOKEN."""
    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {ADMIN_TOKEN}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"refresh_token": _state.get("refresh_token")})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port)
