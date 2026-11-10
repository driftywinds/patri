"""patri: NTES running-status API and web UI.

Run from the repository root:  python -m server.app
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import time
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
API_LIMIT = 120  # requests per minute per client, protects NTES and d9
LOGIN_LIMIT = 5  # attempts per five minutes per client

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

cache = None
_font = {"value": None, "at": 0.0}
_limits = {}


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


def api_search(query):
    query = (query or "").strip()
    key = query.lower()
    hit = cache.get("search", key)
    if cache.fresh(hit):
        return hit["payload"]
    data = rail.search(query)
    cache.put("search", key, data, row_id=(hit or {}).get("id"))
    return data


def api_train(number, force=False):
    number = (number or "").strip()
    if not number.isdigit():
        raise ValueError("train number must be digits")
    hit = cache.get("live", number)
    if hit and not force and cache.fresh(hit):
        return hit["payload"]
    data = rail.page(number)
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


def _secret():
    return os.environ.get("ADMIN_TOKEN", "").encode()


def _sign(expiry):
    return hmac.new(_secret(), expiry.encode(), hashlib.sha256).hexdigest()


def new_session():
    expiry = str(int(time.time()) + SESSION_TTL)
    return f"{expiry}.{_sign(expiry)}"


def session_valid(value):
    if not value or "." not in value or not _secret():
        return False
    expiry, _, sig = value.partition(".")
    try:
        if int(expiry) < time.time():
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign(expiry))


def rate_ok(client, bucket, limit, window):
    now = time.time()
    key = (bucket, client)
    hits = [t for t in _limits.get(key, ()) if now - t < window]
    if len(hits) >= limit:
        _limits[key] = hits
        return False
    hits.append(now)
    _limits[key] = hits
    if len(_limits) > 5000:
        _limits.clear()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "patri/0.1"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        url = urlparse(self.path)
        started = time.time()
        try:
            if url.path.startswith("/api/"):
                self._api(url)
            else:
                self._static(url.path)
        except (BrokenPipeError, ConnectionResetError):
            return
        log.info("%s %.0fms", self.path, (time.time() - started) * 1000)

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self._json(413, {"error": "body too large"})
        raw = self.rfile.read(length) if length else b""

        if url.path == "/api/admin/login":
            return self._login(raw)
        if url.path == "/api/admin/logout":
            return self._json(200, {"admin": False}, self._set_cookie("", 0))
        if url.path == "/api/settings":
            return self._set_setting(raw)
        return self._json(404, {"error": "no such endpoint"})

    # -- admin ------------------------------------------------------------

    def _login(self, raw):
        if not rate_ok(self._client(), "login", LOGIN_LIMIT, 300):
            return self._json(429, {"error": "too many attempts, wait a few minutes"})
        expected = os.environ.get("ADMIN_TOKEN", "")
        if not expected:
            return self._json(503, {"error": "ADMIN_TOKEN is not set on the server"})
        try:
            given = str(json.loads(raw or b"{}").get("token", ""))
        except ValueError:
            return self._json(400, {"error": "invalid json"})
        if not hmac.compare_digest(given, expected):
            time.sleep(0.25)
            return self._json(401, {"error": "wrong admin key"})
        return self._json(200, {"admin": True}, self._set_cookie(new_session(), SESSION_TTL))

    def _set_setting(self, raw):
        if not self._is_admin():
            return self._json(401, {"error": "sign in first"})
        try:
            value = str(json.loads(raw or b"{}").get("font", "")).strip()
            set_font(value)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            log.exception("settings failed: %s", e)
            return self._json(502, {"error": f"could not save: {str(e)[:140]}"})
        log.info("font set to %s", value)
        return self._json(200, {"font": value})

    def _is_admin(self):
        return session_valid(self._read_cookie(SESSION_COOKIE))

    def _read_cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _set_cookie(self, value, max_age):
        attrs = f"{SESSION_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            attrs += "; Secure"
        return {"Set-Cookie": attrs}

    # -- api --------------------------------------------------------------

    def _api(self, url):
        if url.path == "/api/health":
            return self._json(200, {"ok": True})
        if not rate_ok(self._client(), "api", API_LIMIT, 60):
            return self._json(429, {"error": "slow down"})

        try:
            if url.path == "/api/settings":
                return self._json(
                    200, {"font": current_font(), "admin": self._is_admin()}
                )

            if url.path == "/api/search":
                params = parse_qs(url.query)
                data = api_search((params.get("q") or [""])[0])
                return self._json(200, data)

            match = re.fullmatch(r"/api/train/([0-9]{3,5})", url.path)
            if match:
                force = parse_qs(url.query).get("refresh", [""])[0] == "1"
                return self._json(200, api_train(match.group(1), force))

            return self._json(404, {"error": "no such endpoint"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except LookupError as e:
            return self._json(404, {"error": str(e)})
        except Exception as e:
            log.exception("api failed: %s", e)
            return self._json(502, {"error": f"upstream unavailable: {str(e)[:160]}"})

    def _client(self):
        """Real client behind our own reverse proxy, without trusting spoofed headers."""
        peer = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            try:
                if ipaddress.ip_address(peer).is_private or ipaddress.ip_address(peer).is_loopback:
                    return forwarded.split(",")[0].strip()
            except ValueError:
                pass
        return peer

    # -- static -----------------------------------------------------------

    def _static(self, path):
        if path in ("/admin", "/admin/"):
            return self._index()

        rel = path.lstrip("/") or "index.html"
        if rel == "index.html":
            return self._index()

        target = (WEB / rel).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            return self._json(404, {"error": "not found"})
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable"
            if rel.startswith("assets/") or rel.startswith("fonts/")
            else "no-cache"
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
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", CSP)
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    # Windows SO_REUSEADDR would let a second process silently share the port.
    allow_reuse_address = False


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


def main():
    global cache
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

    cache = Cache(base_url, token, int(os.environ.get("CACHE_TTL", "900")))
    if not cache.ready_or_retry():
        log.warning("d9 is not ready - retrying the schema check every 5 minutes")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    if not os.environ.get("ADMIN_TOKEN"):
        log.warning("ADMIN_TOKEN unset - /admin cannot be unlocked")

    print(
        f"\n  patri  http://127.0.0.1:{port}\n"
        f"  admin  http://127.0.0.1:{port}/admin\n"
        f"  lan    http://{_lan_ip()}:{port}\n",
        flush=True,
    )
    Server((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
