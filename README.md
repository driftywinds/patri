# patri

Live Indian Railways running status: search a train by **name or number**, then see
its whole route with scheduled times, actual/expected times, platform and delay —
NTES first, cached in d9 (the Directus fork).

Mobile-first, also fine on desktop. One Svelte page, one Python process.

## What it shows

- Delay banner at the top ("Running 35 min late", last reported station, cache age),
  announced politely to screen readers.
- Every stop: scheduled arrival/departure, actual or expected time, delay chip,
  platform, distance, and a `day 2` marker for after-midnight stops.
- A white marker on the train's current or next stop, derived from stop times in
  IST because NTES drops the arrived/departed flags on individual stops.
- Cancellation / diversion / reschedule alerts when NTES reports them.
- **Followed trains** (saved in `localStorage`) with live delay chips on the home
  screen.
- Auto-refresh every 60 s while the tab is visible, and a **Share** button that
  uses the native share sheet where available.
- The last payload you opened keeps working offline via a small service worker.

Trains that left yesterday and are still running are found through NTES's own
instance list, so journeys longer than two days are handled. Trains that do not
run at all today fall back to the timetable and the next date they run.

## Layout

```
server/app.py    HTTP server, API, static files, security, admin session
server/rail.py   NTES client wrapper and normalisation
server/store.py  cache over d9, collection bootstrap, purge
web/             Svelte 5 UI (built to web/dist)
tests/           unittest suite, no network needed
```

## Run

```bash
cp .env.example .env            # DIRECTUS_URL, DIRECTUS_TOKEN, ADMIN_TOKEN
pip install -r requirements.txt # pinned and hashed

cd web && pnpm install && pnpm build && cd ..

python -m server.app            # -> http://127.0.0.1:8000
```

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The image builds the UI in a Node stage and runs one non-root Python process.
It is read-only, drops all capabilities, and binds to `127.0.0.1:8000` — put
Caddy/nginx in front for TLS.

**`TRUSTED_PROXY=1` is set in compose** because the proxy is the only way in.
It makes the server read the *rightmost* `X-Forwarded-For` hop for rate limiting.
Never enable it when the port is exposed directly.

`GET /api/health` needs neither d9 nor NTES, so it is the container healthcheck.
`GET /api/ready` additionally reports d9 and NTES state.

## Configuration

| variable | default | meaning |
|---|---|---|
| `DIRECTUS_URL` | — | d9 base URL; must be `https` (http only for localhost) |
| `DIRECTUS_TOKEN` | — | d9 token; CRUD on `patri_cache` is enough |
| `ADMIN_TOKEN` | — | unlocks `/admin`; under 32 chars disables it |
| `TRUSTED_PROXY` | `0` | trust the rightmost `X-Forwarded-For` hop |
| `NTES_CONCURRENCY` | `4` | max simultaneous NTES calls |
| `CACHE_TTL` | `900` | seconds a cached train payload is served |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | listen address |

## d9 setup

The app owns exactly one collection, `patri_cache`, and creates it with its
fields on first boot when the token is allowed to manage schema. With a
restricted token, create it once by hand in the d9 admin UI (or give the token
schema access for one boot). Nothing else is touched.

| field | type | notes |
|---|---|---|
| `id` | integer, PK, auto | created by d9 |
| `kind` | string | `live`, `search`, `exc`, `miss`, `setting` |
| `cache_key` | string | train number, normalised query, or `font` |
| `train_no` | string, nullable | for filtering in the admin UI |
| `run_date` | date, nullable | which run the cached page describes |
| `payload` | json | the API response the UI gets |

Rows are purged after 7 days (the `setting` row is never touched), and a
five-minute retry re-checks the schema if d9 was down at boot.

## Admin

`/admin` switches the site typeface (Inter by default, IBM Plex Sans optional),
stored as a `setting` row and injected into `<html data-font>` while the HTML is
served — no flash, no extra request.

The key is traded for a random session id kept server-side, in an `HttpOnly`,
`SameSite=Strict` cookie (`__Host-` prefixed over HTTPS). Logout deletes the
session, so it revokes immediately; restarting the server also ends every
session. `/admin` returns 404 when no valid `ADMIN_TOKEN` is configured.

## API

```
GET  /api/train/<no>?refresh=1   one page: header, status, stops, alerts
GET  /api/search?q=...           name or number
GET  /api/settings               current font + whether you are signed in
POST /api/settings               {"font":"inter|plex"}  (session required)
POST /api/admin/login            {"token":"..."}        (rate limited)
POST /api/admin/logout
GET  /api/health                 liveness only
GET  /api/ready                  d9 + NTES readiness
GET  /api/metrics                plain-text counters
```

Upstream protection: forced refresh is allowed once per train per 60 s,
failures are negatively cached for 3 minutes, identical concurrent misses share
one NTES call, and NTES calls are capped globally. A stale payload is served
rather than an error when NTES fails.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

No network: the NTES normaliser is table-driven, and the handler tests cover the
malformed-`Content-Length`, NUL-path, cross-origin and spoofed-`X-Forwarded-For`
cases found in review.

## Security notes

- The d9 token stays server-side and is sent to `https` URLs only, with
  redirects refused, so it cannot be forwarded to another host.
- Rate limits are per real client, evicted least-recently-used rather than wiped,
  and IPv6 clients are keyed by `/64`.
- Admin sessions are random ids in server state (not signed with the admin
  password), and state-changing requests require a JSON `Content-Type` and a
  same-origin `Origin`.
- CSP keeps `style-src 'unsafe-inline'` because Svelte compiles per-component
  style attributes; there are no third-party origins and fonts are self-hosted.
- `requirements.txt` pins every version with hashes; the Docker bases are digest
  pinned and CI audits both ecosystems.

## Known gaps

- No Web Push alerts (needs VAPID keys, a subscription table and a background
  poller), no PNR lookup (captcha-based, personal data), no Hindi UI yet.
- No date picker on the train page; the API always picks the nearest live run.
- `http.server` is still the server, now with timeouts, a worker cap and a
  catch-all; a WSGI/ASGI server is the next step if traffic grows.

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
