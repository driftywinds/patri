"""Cache on top of the d9 `patri_cache` collection.

The collection is created on first boot when the token is allowed to manage
schema, so a fresh d9 instance needs no manual setup. Everything here is
best-effort: a cache failure is logged and the request carries on.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("patri.cache")

COLLECTION = "patri_cache"

# d9 usually boots slower than us; keep checking until the collection answers.
RETRY_SECONDS = 300
# A cached page is stale after 15 minutes, so a row idle for two days is noise.
PURGE_HOURS = 48

FIELDS = [
    {
        "field": "kind",
        "type": "string",
        "meta": {
            "interface": "select-dropdown",
            "options": {
                "choices": [
                    {"text": "live", "value": "live"},
                    {"text": "search", "value": "search"},
                    {"text": "setting", "value": "setting"},
                    {"text": "miss", "value": "miss"},
                ]
            },
            "width": "half",
        },
        "schema": {},
    },
    {"field": "cache_key", "type": "string", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    {"field": "train_no", "type": "string", "meta": {"interface": "input", "width": "half"}, "schema": {}},
    {"field": "run_date", "type": "date", "meta": {"interface": "datetime", "width": "half"}, "schema": {}},
    {"field": "payload", "type": "json", "meta": {"interface": "input-code", "options": {"language": "json"}}, "schema": {}},
    {
        "field": "date_created",
        "type": "timestamp",
        "meta": {"special": ["date-created"], "interface": "datetime", "readonly": True, "width": "half"},
        "schema": {},
    },
    {
        "field": "date_updated",
        "type": "timestamp",
        "meta": {"special": ["date-updated"], "interface": "datetime", "readonly": True, "width": "half"},
        "schema": {},
    },
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the bearer token must not travel to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_local(host):
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


class Cache:
    def __init__(self, base_url: str, token: str, ttl: int):
        base = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme == "https":
            pass
        elif parsed.scheme == "http" and _is_local(parsed.hostname or ""):
            pass
        else:
            raise ValueError("DIRECTUS_URL must be https (http is only allowed for localhost)")
        self.base = base
        self.endpoint = f"{self.base}/items/{COLLECTION}"
        self.token = token
        self.ttl = ttl
        self.ready = False
        self._next_check = 0.0
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- plumbing -------------------------------------------------------

    def _headers(self):
        return {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare in front of d9 rejects the default urllib agent.
            "User-Agent": "Mozilla/5.0 (compatible; patri/0.1)",
        }

    def _raw(self, method, path, body=None):
        """(status, text). Never raises: schema calls need the status code."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=self._headers()
        )
        try:
            with self._opener.open(req, timeout=15) as r:
                return r.status, r.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, str(e)

    def _call(self, method, path="", body=None):
        status, text = self._raw(method, f"/items/{COLLECTION}{path}", body)
        if status not in (200, 204):
            raise RuntimeError(f"{method} {path} -> {status}: {text[:160]}")
        return json.loads(text or "{}")

    # -- schema ---------------------------------------------------------

    def ensure_schema(self):
        """Make sure the collection and every field this version needs exist."""
        status, _ = self._raw("GET", f"/items/{COLLECTION}?limit=1")
        if status == 200:
            self._repair_fields()
            return True
        if status not in (403, 404):
            log.warning("could not check %s (status %s)", COLLECTION, status)
            return False

        # 403 is this fork's answer for both "missing" and "not allowed", so try.
        status, text = self._raw(
            "POST",
            "/collections",
            {
                "collection": COLLECTION,
                "meta": {"icon": "database", "note": "patri: train data cache (owned by the patri app)"},
                "schema": {},
            },
        )
        if status != 200:
            if "already exists" in text.lower():
                log.warning("%s exists but the token cannot read it", COLLECTION)
            else:
                log.warning(
                    "could not create %s (%s): %s - create it manually or use a token "
                    "with schema access",
                    COLLECTION,
                    status,
                    text[:160],
                )
            return False

        for field in FIELDS:
            status, text = self._raw("POST", f"/fields/{COLLECTION}", field)
            if status != 200:
                log.warning("field %s failed (%s): %s", field["field"], status, text[:160])
                return False
        log.info("created collection at d9 server %s", self.base)
        return True

    def _repair_fields(self):
        """Add fields an older collection is missing; a read-only token is left alone."""
        status, text = self._raw("GET", f"/fields/{COLLECTION}")
        if status != 200:
            return
        try:
            present = {f["field"] for f in json.loads(text or "{}").get("data", [])}
        except ValueError:
            return
        added = 0
        for field in FIELDS:
            if field["field"] in present:
                continue
            status, text = self._raw("POST", f"/fields/{COLLECTION}", field)
            if status == 200:
                added += 1
            elif "already exists" not in text.lower():
                log.warning("field %s could not be added (%s)", field["field"], status)
        if added:
            log.info("added %d field(s) to %s", added, COLLECTION)

    def ready_or_retry(self):
        """True once the collection is usable; re-checks at most every 5 minutes."""
        if self.ready:
            return True
        with self._lock:
            if self.ready:
                return True
            now = time.time()
            if now < self._next_check:
                return False
            self._next_check = now + RETRY_SECONDS
            try:
                self.ready = self.ensure_schema()
            except Exception as e:
                log.warning("schema check failed: %s", e)
                self.ready = False
            if self.ready:
                log.info("successfully connected to d9 at %s", self.base)
            return self.ready

    def purge(self, hours=PURGE_HOURS):
        """Drop rows idle for `hours`; the setting row is never touched.

        date_updated is only set when a row is rewritten, so a row that is still
        being read keeps its date_updated fresh and survives.
        """
        if not self.ready:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        query = urllib.parse.urlencode(
            {
                "filter[_or][0][date_updated][_lt]": cutoff,
                "filter[_or][1][_and][0][date_updated][_null]": "true",
                "filter[_or][1][_and][1][date_created][_lt]": cutoff,
                "filter[kind][_neq]": "setting",
                "fields": "id",
                "limit": -1,
            }
        )
        try:
            rows = self._call("GET", "?" + query).get("data") or []
        except Exception as e:
            log.warning("purge lookup failed: %s", e)
            return 0

        ids = [row["id"] for row in rows if row.get("id") is not None]
        removed = 0
        # This fork deletes by keys in the JSON body, not by query string.
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            try:
                self._call("DELETE", "", {"keys": chunk})
                removed += len(chunk)
            except Exception as e:
                log.warning("purge delete failed: %s", e)
                break
        if removed:
            log.info("purged %d cache rows idle for %d h", removed, hours)
        return removed

    # -- cache ----------------------------------------------------------

    def get(self, kind, key):
        """Return {'id': int, 'payload': dict} or None."""
        if not self.ready_or_retry():
            return None
        query = urllib.parse.urlencode(
            {
                "filter[kind][_eq]": kind,
                "filter[cache_key][_eq]": key,
                "limit": 1,
                "fields": "id,payload",
            }
        )
        try:
            rows = self._call("GET", "?" + query).get("data") or []
        except Exception as e:
            log.warning("cache read failed: %s", e)
            return None
        if not rows:
            return None
        payload = rows[0].get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        return {"id": rows[0].get("id"), "payload": payload}

    def put(self, kind, key, payload, train_no=None, run_date=None, row_id=None):
        if not self.ready_or_retry():
            return
        body = {
            "kind": kind,
            "cache_key": key,
            "payload": payload,
            "train_no": train_no,
            "run_date": run_date or None,
        }
        try:
            if row_id is not None:
                self._call("PATCH", f"/{row_id}", body)
            else:
                self._call("POST", "", body)
        except Exception as e:
            log.warning("cache write failed: %s", e)

    def fresh(self, hit, ttl=None):
        if not hit:
            return False
        try:
            age = time.time() - float(hit["payload"].get("fetched_at") or 0)
        except (TypeError, ValueError):
            return False
        return age < (self.ttl if ttl is None else ttl)
