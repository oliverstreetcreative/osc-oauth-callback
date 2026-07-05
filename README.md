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
   `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `PUBLIC_BASE_URL`
   (= `https://osc-oauth-callback-production.up.railway.app`),
   `GITHUB_ALLOWED_USERS` (default `oliverstreetcreative`), `ADMIN_TOKEN`.
   The GitHub OAuth app is **separate from the Fastmail proxy's** — classic
   GitHub OAuth Apps only support one callback URL each, and this server's
   callback (`<PUBLIC_BASE_URL>/auth/callback`) differs from Fastmail's.
   Secrets live in this machine's Keychain: `kroger-client-id`,
   `kroger-client-secret`, `github-oauth-osc-kroger` (client id + secret),
   `osc-kroger-admin-token`.

2. **Bootstrap cart access** (needed once, or again if the refresh token is
   ever lost — product/location search work immediately without this step):
   - Call the `kroger_get_authorize_url` tool.
   - Sam (only Sam — it's his account) opens that URL, logs into Kroger,
     grants access.
   - Kroger redirects to `/kroger/callback` here, which exchanges the code for
     tokens and holds the refresh_token in memory.
   - Retrieve it once: `curl -H "Authorization: Bearer $ADMIN_TOKEN" https://osc-oauth-callback-production.up.railway.app/admin/refresh-token`
   - Persist it so it survives restarts: `railway variable set KROGER_REFRESH_TOKEN --stdin --service osc-oauth-callback` (piping the value in, never as a bare CLI arg).

3. **Add as a Claude connector**: `claude mcp add --transport http osc-kroger https://osc-oauth-callback-production.up.railway.app/mcp` (no header needed — it does a real GitHub login via the browser, same as the Fastmail proxy).

## Auth model

Two independent OAuth flows, serving different purposes:
- **Front door** (MCP tools): GitHub OAuth restricted to an allowlist of
  logins — identical pattern to the Fastmail proxy. Upgraded to this from an
  earlier static-bearer-token design once it was clear a shared secret
  sitting in a config file was a real (if modest) liability compared to a
  real per-identity login with no static secret to leak.
- **Kroger auth**: real OAuth2 against Kroger's API, unrelated to the above —
  see server.py's docstring for the full flow.
- `/kroger/callback` and `/admin/refresh-token` are intentionally outside the
  GitHub-OAuth-protected surface (Kroger's server can't do a GitHub login) —
  protected instead by the OAuth `state` CSRF check (callback) and a separate
  static `ADMIN_TOKEN` (admin endpoint, operator/CLI-only, not something
  Claude ever calls).

## Known limitations (accepted tradeoffs for a personal single-user tool)

- Refresh token isn't auto-persisted on rotation — if Kroger ever rotates it
  out from under a running process, redo step 2. Fine for one user; would need
  real persistence (a Railway volume, or the server self-updating its own env
  var via the Railway API) for anything more than that.
- No checkout/payment — see above, this is a Kroger API tier limitation.
