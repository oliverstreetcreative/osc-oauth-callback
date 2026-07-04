# osc-oauth-callback → OSC Kroger Grocery Assistant

Was a 30-line stub that emailed Sam a raw Kroger OAuth code to paste into a
tool ("Studio Manager") that never got built. Rewritten 2026-07-04 into a real
MCP server — kept the same repo/Railway service so the redirect URI already
registered with Kroger didn't need to change.

## What it does

Claude can search Kroger products/locations and add items to Sam's real
Kroger cart. **Checkout/payment is not possible via the public API** — Sam
still has to open kroger.com or the Kroger app to actually pay. This is a
Kroger platform limitation, not something this code can work around.

## One-time setup

1. **Deploy** with these env vars set on the Railway service:
   `KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET`, `KROGER_REDIRECT_URI`
   (= `https://osc-oauth-callback-production.up.railway.app/kroger/callback`),
   `MCP_BEARER_TOKEN`, `ADMIN_TOKEN`. All four secrets live in this machine's
   Keychain: `kroger-client-id`, `kroger-client-secret`,
   `osc-kroger-mcp-bearer`, `osc-kroger-admin-token`.

2. **Bootstrap cart access** (needed once, or again if the refresh token is
   ever lost — product/location search work immediately without this step):
   - Call the `kroger_get_authorize_url` tool.
   - Sam (only Sam — it's his account) opens that URL, logs into Kroger,
     grants access.
   - Kroger redirects to `/kroger/callback` here, which exchanges the code for
     tokens and holds the refresh_token in memory.
   - Retrieve it once: `curl -H "Authorization: Bearer $ADMIN_TOKEN" https://osc-oauth-callback-production.up.railway.app/admin/refresh-token`
   - Persist it so it survives restarts: `railway variable set KROGER_REFRESH_TOKEN --stdin --service "OSC - Kroger Grocery Assistant"` (piping the value in, never as a bare CLI arg).

3. **Add as a Claude connector** using `MCP_BEARER_TOKEN` as the bearer token.

## Auth model

Two independent layers, deliberately different shapes because they protect
different things:
- **Front door** (MCP tools): a single static bearer token (`MCP_BEARER_TOKEN`).
  Simpler than the Fastmail proxy's GitHub-OAuth-allowlist since this doesn't
  need per-human identity, just needs to not be open to the whole internet.
- **Kroger auth**: real OAuth2 against Kroger's API — see server.py's
  docstring for the full flow.
- `/kroger/callback` and `/admin/refresh-token` are intentionally outside the
  bearer-token-protected surface (Kroger's server can't present our bearer
  token) — `/admin/refresh-token` has its own separate secret instead.

## Known limitations (accepted tradeoffs for a personal single-user tool)

- Refresh token isn't auto-persisted on rotation — if Kroger ever rotates it
  out from under a running process, redo step 2. Fine for one user; would need
  real persistence (a Railway volume, or the server self-updating its own env
  var via the Railway API) for anything more than that.
- No checkout/payment — see above, this is a Kroger API tier limitation.
