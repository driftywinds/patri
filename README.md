# patri

Live Indian Railways running status: search a train by **name or number**, then see
its whole route with scheduled times, actual/expected times, platform and delay —
NTES first, cached in d9 (Directus 9).

Mobile-first, also fine on desktop. One Svelte page, one Python process.

## What it shows

- Delay banner at the top ("Running 35 min late", last reported station, cache age).
- Every stop: scheduled arrival/departure, actual or expected time, delay chip,
  platform, distance, and a `+1` day marker for overnight legs.
- Trains that left yesterday and are still running are detected automatically.

Expected times come from NTES itself (its `ETA` already folds in the running
delay), so nothing is re-derived locally.

## d9 setup

The app owns exactly one collection, `patri_cache` (nothing else is touched):

| field | type | notes |
|---|---|---|
| `id` | integer, PK, auto | created by Directus |
| `kind` | string | `live`, `search` or `setting` |
| `cache_key` | string | train number, or `name:<query>` / `number:<query>` |
| `train_no` | string, nullable | for filtering in the admin UI |
| `run_date` | date, nullable | which run the cached page describes |
| `payload` | json | the API response the UI gets |

Plus the two Directus timestamps. Rows are keyed by `kind` + `cache_key`; the
server upserts on `cache_key` and never writes anywhere else. The static token
needs **create/read/update/delete on `patri_cache` only**.

To reproduce it through the API:

```python
POST /collections  {"collection": "patri_cache", "meta": {"icon": "database"}, "schema": {}}
POST /fields/patri_cache  {"field": "kind",      "type": "string", "meta": {"interface": "input"}, "schema": {}}
POST /fields/patri_cache  {"field": "cache_key", "type": "string", "meta": {"interface": "input"}, "schema": {}}
POST /fields/patri_cache  {"field": "train_no",  "type": "string", "meta": {"interface": "input"}, "schema": {}}
POST /fields/patri_cache  {"field": "run_date",  "type": "date",   "meta": {"interface": "datetime"}, "schema": {}}
POST /fields/patri_cache  {"field": "payload",   "type": "json",   "meta": {"interface": "input-code"}, "schema": {}}
```

## Run

```bash
cp .env.example .env            # fill DIRECTUS_TOKEN
pip install -r requirements.txt

cd web && pnpm install && pnpm build && cd ..

python -m server.app            # -> http://127.0.0.1:8000  (and your LAN IP)
```

`web/` is built to `web/dist`, which the Python server serves. For UI work,
`cd web && pnpm dev` proxies `/api` to port 8000.

## Admin

`/admin` switches the site typeface (Inter by default, IBM Plex Sans optional).
The choice is stored as a `setting` row in `patri_cache` and injected into
`<html data-font>` while the HTML is served, so every page and every device gets
it with no flash and no extra request.

Writing the setting requires the `ADMIN_TOKEN` from `.env`, sent as an
`X-Admin-Token` header. The page asks for it once and keeps it in
`localStorage`. With no token set on the server, `/admin` can read but not save.


## Look and feel

Pitch black canvas, grey hairlines, white for anything that matters. No hue is
used to carry meaning: a delay is white-on-black text plus a filled chip, "on
time" is a quiet outline, so the page reads the same in any light.

Type is Inter by default and switchable at `/admin` — see below. Both families
load from Google Fonts; offline the UI falls back to the system sans. See
`mockups/` for the two side by side.

## How fresh it is

Every page read uses a cached row younger than `CACHE_TTL` (default 900 s), so
what you see is at most 15 minutes old and NTES is only hit when it expires or
when you press **Refresh**. There is no background poller: with no saved trains
or notifications there is nothing to warm, and it keeps the request count low.
Add the poller when you add followed trains.

## Deploy

```bash
cp .env.example .env    # DIRECTUS_URL, DIRECTUS_TOKEN, ADMIN_TOKEN
docker compose up -d --build
```

The image builds the UI in a Node stage and runs a single non-root Python
process. The container is read-only with no capabilities, and compose binds it
to `127.0.0.1:8000` — put Caddy/nginx in front for TLS and change the bind to
`8000:8000` only if it must be reachable directly.

`GET /api/health` needs neither d9 nor NTES, so it is safe as the healthcheck.

## Security

- **The app's d9 token is not an admin token.** It belongs to a `patri app` role
  with CRUD on `patri_cache` and nothing else: schema, users and every other
  collection return 403. The admin token is only needed to change that role.
- **The admin key never goes to the browser.** `/api/admin/login` trades it for
  a signed, `HttpOnly`, `SameSite=Strict` session cookie; the key itself is never
  stored client-side and the session expires after 12 h.
- **Rate limits** — 120 API requests/minute per client (protects NTES from us),
  5 failed logins per 5 minutes, both keyed on the real client IP when the peer
  is a private address (our own proxy), never on a spoofable header from the
  internet.
- **CSP without third-party origins** — fonts are self-hosted in
  `web/public/fonts`, so `font-src`/`style-src` stay `'self'` and there is no
  Google Fonts request.
- `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, HSTS when
  served over HTTPS.

## Notes

- NTES has no public API. This uses the reverse-engineered mobile API through
  [`ntes-client`](https://pypi.org/project/ntes-client/) (MIT). It can break
  without notice.
- The Directus token lives in `.env`, which is gitignored. The browser never
  sees it: the app only talks to this server.
- Request rate is naturally low (~1-2 NTES calls per cold train, then cached).
