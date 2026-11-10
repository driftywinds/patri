"""patri: NTES running-status API and web UI.

Run from the repository root:  python -m server.app
"""
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import rail
from .store import Cache

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "dist"

log = logging.getLogger("patri")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".webmanifest": "application/manifest+json",
    ".map": "application/json",
}

FONTS = ("inter", "plex")
DEFAULT_FONT = "inter"

SESSION_COOKIE = "patri_admin"
SESSION_TTL = 12 * 3600
SESSION_MAX = 100
API_LIMIT = 120  # requests per minute per client
LOGIN_LIMIT = 5  # attempts per five minutes per client
REFRESH_MIN = 60  # seconds between forced refreshes of one train
MISS_TTL = 180  # negative cache for "no such train" / upstream failure
EXC_TTL = 6 * 3600  # cancellations change rarely
MIN_ADMIN_TOKEN = 32

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

cache = None
_font = {"value": None, "at": 0.0}

_sessions = {}
_sessions_lock = threading.Lock()

_locks = [threading.Lock() for _ in range(64)]
_api_limiter = None
_login_limiter = None

_metrics = {"api": 0, "cache_hit": 0, "cache_miss": 0, "errors": 0}
_metrics_lock = threading.Lock()


def _count(name, n=1):
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n


class RateLimiter:
    """Fixed-window limiter that evicts the oldest key instead of wiping itself."""

    def __init__(self, max_keys=20000):
        self._hits = OrderedDict()
        self._max = max_keys
        self._lock = threading.Lock()

    def allow(self, key, limit, window):
        now = time.time()
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = []
            else:
                self._hits.move_to_end(key)

            if bucket and now - bucket[-1] >= window:
                bucket = []  # everything in the bucket expired
            else:
                bucket = [t for t in bucket if now - t < window]

            if len(bucket) >= limit:
                self._hits[key] = bucket
                self._hits.move_to_end(key)
                return False

            bucket.append(now)
            self._hits[key] = bucket
            self._hits.move_to_end(key)
            while len(self._hits) > self._max:
                self._hits.popitem(last=False)
            return True


def _key_lock(key):
    return _locks[hash(key) % len(_locks)]


def _trusted_proxy():
    return os.environ.get("TRUSTED_PROXY", "0").strip().lower() in ("1", "true", "yes")


def _norm_ip(text):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def client_ip(peer, forwarded):
    """The real client, without trusting a header a client can write."""
    if not _trusted_proxy() or not forwarded:
        return peer
    # Our proxy appends the address it saw, so the rightmost hop is the client.
    candidate = _norm_ip(forwarded.split(",")[-1])
    return str(candidate) if candidate else peer


def rate_key(ip):
    """Collapse IPv6 to its /64 so one host cannot rotate through its subnet."""
    parsed = _norm_ip(ip)
    if parsed is not None and parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return ip


# --- settings ---------------------------------------------------------------


def current_font(refresh=False):
    """The site typeface, held in d9 so it survives restarts and devices."""
    if not refresh and _font["value"] and time.time() - _font["at"] < 30:
        return _font["value"]
    hit = cache.get("setting", "font")
    value = ((hit or {}).get("payload") or {}).get("value")
    if value not in FONTS:
        value = DEFAULT_FONT
    _font.update(value=value, at=time.time())
    return value


def set_font(value):
    if value not in FONTS:
        raise ValueError("font must be one of: " + ", ".join(FONTS))
    hit = cache.get("setting", "font")
    cache.put("setting", "font", {"value": value}, row_id=(hit or {}).get("id"))
    _font.update(value=value, at=time.time())


# --- upstream ---------------------------------------------------------------


def _exceptions(number):
    hit = cache.get("exc", number)
    if cache.fresh(hit, EXC_TTL):
        return hit["payload"].get("rows") or []
    rows = rail.exceptions(number)
    cache.put(
        "exc",
        number,
        {"fetched_at": time.time(), "rows": rows},
        train_no=number,
        row_id=(hit or {}).get("id"),
    )
    return rows


def api_search(query):
    q = rail.clean_query(query)
    if len(q) < 2:
        raise ValueError("enter at least 2 characters")
    key = q.lower()

    hit = cache.get("search", key)
    if cache.fresh(hit):
        _count("cache_hit")
        return hit["payload"]

    with _key_lock("search:" + key):
        hit = cache.get("search", key)
        if cache.fresh(hit):
            _count("cache_hit")
            return hit["payload"]
        _count("cache_miss")
        data = rail.search(q)
        cache.put("search", key, data, row_id=(hit or {}).get("id"))
        return data


def api_train(number, force=False):
    number = (number or "").strip()
    if not number.isdigit():
        raise ValueError("train number must be digits")

    def _fresh(cached):
        if not cached or not cache.fresh(cached):
            return False
        if not force:
            return True
        age = time.time() - float(cached["payload"].get("fetched_at") or 0)
        return age < REFRESH_MIN

    hit = cache.get("live", number)
    if _fresh(hit):
        _count("cache_hit")
        return hit["payload"]

    miss = cache.get("miss", number)
    if cache.fresh(miss, MISS_TTL):
        _count("cache_hit")
        raise LookupError("no live data for this train")

    with _key_lock("train:" + number):
        # Another thread may have filled this while we waited.
        hit = cache.get("live", number)
        if _fresh(hit):
            _count("cache_hit")
            return hit["payload"]

        _count("cache_miss")
        try:
            data = rail.page(number)
            alerts = _exceptions(number)
            if alerts:
                data["status"]["exceptions"] = alerts
        except LookupError:
            _count("errors")
            cache.put("miss", number, {"fetched_at": time.time()}, train_no=number)
            raise
        except Exception:
            _count("errors")
            if hit:
                log.warning("upstream failed for %s, serving the cached copy", number)
                return hit["payload"]
            cache.put("miss", number, {"fetched_at": time.time()}, train_no=number)
            raise

        cache.put(
            "live",
            number,
            data,
            train_no=number,
            run_date=data["train"]["run_date"],
            row_id=(hit or {}).get("id"),
        )
        return data


# --- admin session ----------------------------------------------------------


def _admin_enabled():
    return len(os.environ.get("ADMIN_TOKEN", "")) >= MIN_ADMIN_TOKEN


def _new_session():
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        _sessions[sid] = now + SESSION_TTL
        if len(_sessions) > SESSION_MAX:
            for stale in sorted(_sessions, key=_sessions.get)[: len(_sessions) - SESSION_MAX]:
                _sessions.pop(stale, None)
    return sid


def _session_valid(sid):
    if not sid:
        return False
    now = time.time()
    with _sessions_lock:
        expiry = _sessions.get(sid)
        if expiry is None:
            return False
        if expiry < now:
            _sessions.pop(sid, None)
            return False
        return True


def _session_drop(sid):
    if sid:
        with _sessions_lock:
            _sessions.pop(sid, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "patri/0.1"
    protocol_version = "HTTP/1.1"
    timeout = 12

    def do_GET(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def _run(self, method):
        started = time.time()
        path = urlparse(self.path).path
        self._status = 0
        try:
            url = urlparse(self.path)
            if method == "GET":
                self._api(url) if url.path.startswith("/api/") else self._static(url.path)
            else:
                self._post(url)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            log.exception("unhandled %s %s", method, path)
            try:
                self._json(500, {"error": "internal error"})
            except Exception:
                pass
        finally:
            log.info(
                "%s %s %s %d %dms",
                self._client(),
                method,
                path,
                self._status,
                (time.time() - started) * 1000,
            )

    # -- helpers ----------------------------------------------------------

    def _https(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _client(self):
        try:
            return client_ip(self.client_address[0], self.headers.get("X-Forwarded-For", ""))
        except Exception:
            return self.client_address[0]

    def _cookie_name(self):
        return "__Host-patri_admin" if self._https() else SESSION_COOKIE

    def _read_cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _cookie(self, value, max_age):
        attrs = (
            f"{self._cookie_name()}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        )
        if self._https():
            attrs += "; Secure"
        return {"Set-Cookie": attrs}

    def _is_admin(self):
        return _session_valid(self._read_cookie(self._cookie_name()))

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        return bool(parsed.netloc) and parsed.netloc == self.headers.get("Host", "")

    def _read_body(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            return b""
        try:
            length = int(raw)
        except (TypeError, ValueError):
            self._json(400, {"error": "invalid content-length"})
            return None
        if length < 0 or length > 4096:
            self._json(413 if length > 4096 else 400, {"error": "invalid content-length"})
            return None
        return self.rfile.read(length)

    # -- POST -------------------------------------------------------------

    def _post(self, url):
        body = self._read_body()
        if body is None:
            return
        if not self._same_origin() or not self.headers.get(
            "Content-Type", ""
        ).lower().startswith("application/json"):
            return self._json(403, {"error": "forbidden"})

        if url.path == "/api/admin/login":
            return self._login(body)
        if url.path == "/api/admin/logout":
            _session_drop(self._read_cookie(self._cookie_name()))
            return self._json(200, {"admin": False}, self._cookie("", 0))
        if url.path == "/api/settings":
            return self._set_setting(body)
        return self._json(404, {"error": "no such endpoint"})

    def _login(self, body):
        if not _login_limiter.allow(rate_key(self._client()), LOGIN_LIMIT, 300):
            return self._json(429, {"error": "too many attempts, wait a few minutes"})
        expected = os.environ.get("ADMIN_TOKEN", "")
        if not _admin_enabled():
            return self._json(503, {"error": "admin is disabled"})
        try:
            given = str(json.loads(body or b"{}").get("token", ""))
        except ValueError:
            return self._json(400, {"error": "invalid json"})
        if not hmac.compare_digest(given, expected):
            time.sleep(0.25)
            return self._json(401, {"error": "wrong admin key"})
        return self._json(200, {"admin": True}, self._cookie(_new_session(), SESSION_TTL))

    def _set_setting(self, body):
        if not _admin_enabled() or not self._is_admin():
            return self._json(401, {"error": "sign in first"})
        try:
            value = str(json.loads(body or b"{}").get("font", "")).strip()
            set_font(value)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            log.exception("settings failed: %s", e)
            return self._json(502, {"error": "could not save"})
        log.info("font set to %s", value)
        return self._json(200, {"font": value})

    # -- GET --------------------------------------------------------------

    def _api(self, url):
        if url.path == "/api/health":
            return self._json(200, {"ok": True})
        if url.path == "/api/ready":
            return self._ready()
        if url.path == "/api/metrics":
            return self._metrics()

        if not _api_limiter.allow(rate_key(self._client()), API_LIMIT, 60):
            return self._json(429, {"error": "slow down"})
        _count("api")

        try:
            if url.path == "/api/settings":
                return self._json(200, {"font": current_font(), "admin": self._is_admin()})

            if url.path == "/api/search":
                params = parse_qs(url.query)
                return self._json(200, api_search((params.get("q") or [""])[0]))

            match = re.fullmatch(r"/api/train/([0-9]{3,5})", url.path)
            if match:
                force = parse_qs(url.query).get("refresh", [""])[0] == "1"
                return self._json(200, api_train(match.group(1), force))

            return self._json(404, {"error": "no such endpoint"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except LookupError:
            return self._json(404, {"error": "no data for this train"})
        except Exception as e:
            log.exception("api failed: %s", e)
            return self._json(502, {"error": "upstream unavailable"})

    def _ready(self):
        ntes = rail.stats()
        ready = bool(cache and cache.ready)
        payload = {
            "d9": ready,
            "ntes_calls": ntes.get("calls", 0),
            "ntes_last_ok_age_s": (
                round(time.time() - ntes["last_ok"]) if ntes.get("last_ok") else None
            ),
        }
        return self._json(200 if ready else 503, payload)

    def _metrics(self):
        with _metrics_lock:
            counts = dict(_metrics)
        ntes = rail.stats()
        lines = [f"patri_{k} {v}" for k, v in sorted(counts.items())]
        lines.append(f"patri_ntes_calls {ntes.get('calls', 0)}")
        lines.append(f"patri_cache_ready {1 if cache and cache.ready else 0}")
        body = ("\n".join(lines) + "\n").encode()
        self._send(200, body, "text/plain; charset=utf-8", {"Cache-Control": "no-store"})

    # -- static -----------------------------------------------------------

    def _static(self, path):
        if "\x00" in path:
            return self._json(404, {"error": "not found"})
        if path in ("/admin", "/admin/"):
            if not _admin_enabled():
                return self._json(404, {"error": "not found"})
            return self._index()

        rel = path.lstrip("/") or "index.html"
        if rel == "index.html":
            return self._index()

        try:
            target = (WEB / rel).resolve()
        except (OSError, ValueError):
            return self._json(404, {"error": "not found"})
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            return self._json(404, {"error": "not found"})

        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return

        headers = {
            "Cache-Control": "public, max-age=31536000, immutable"
            if rel.startswith("assets/") or rel.startswith("fonts/")
            else "no-cache",
            "ETag": etag,
        }
        self._send(
            200,
            target.read_bytes(),
            MIME.get(target.suffix.lower(), "application/octet-stream"),
            headers,
        )

    def _index(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        # Bake the saved typeface in before first paint: no flash, no extra request.
        html = re.sub(
            r'(<html[^>]*data-font=")[^"]*(")',
            lambda m: m.group(1) + current_font() + m.group(2),
            html,
            count=1,
        )
        self._send(
            200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-cache"}
        )

    # -- responses --------------------------------------------------------

    def _json(self, status, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        merged = {"Cache-Control": "no-store"}
        merged.update(headers or {})
        self._send(status, body, "application/json; charset=utf-8", merged)

    def _send(self, status, body, ctype, headers=None):
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", CSP)
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    # Windows SO_REUSEADDR would let a second process silently share the port.
    allow_reuse_address = False
    daemon_threads = True
    MAX_WORKERS = 64

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.MAX_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _janitor():
    while True:
        time.sleep(6 * 3600)
        try:
            cache.purge()
        except Exception as e:
            log.warning("janitor failed: %s", e)


def main():
    global cache, _api_limiter, _login_limiter
    _load_env(ROOT / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    base_url = os.environ.get("DIRECTUS_URL", "").rstrip("/")
    token = os.environ.get("DIRECTUS_TOKEN", "").strip()
    if not base_url or not token:
        raise SystemExit("set DIRECTUS_URL and DIRECTUS_TOKEN (copy .env.example to .env)")
    if not WEB.is_dir():
        raise SystemExit(f"{WEB} is missing - build the UI first: cd web && pnpm install && pnpm build")

    try:
        cache = Cache(base_url, token, int(os.environ.get("CACHE_TTL", "900")))
    except ValueError as e:
        raise SystemExit(str(e))

    _api_limiter = RateLimiter()
    _login_limiter = RateLimiter()

    if not cache.ready_or_retry():
        log.warning("d9 is not ready - retrying the schema check every 5 minutes")
    else:
        cache.purge()
    threading.Thread(target=_janitor, daemon=True).start()

    admin = os.environ.get("ADMIN_TOKEN", "")
    if not admin:
        log.warning("ADMIN_TOKEN unset - /admin is disabled")
    elif len(admin) < MIN_ADMIN_TOKEN:
        log.warning("ADMIN_TOKEN is shorter than %d chars - /admin is disabled", MIN_ADMIN_TOKEN)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    print(
        f"\n  patri  http://127.0.0.1:{port}\n"
        f"  admin  http://127.0.0.1:{port}/admin\n"
        f"  lan    http://{_lan_ip()}:{port}\n",
        flush=True,
    )
    Server((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
