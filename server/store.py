"""Cache on top of the d9 `patri_cache` collection.

The collection is created on first boot when the token is allowed to manage
schema, so a fresh d9 instance needs no manual setup. Everything here is
best-effort: a cache failure is logged and the request carries on.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("patri.cache")

COLLECTION = "patri_cache"

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
]


class Cache:
    def __init__(self, base_url: str, token: str, ttl: int):
        self.base = base_url.rstrip("/")
        self.endpoint = f"{self.base}/items/{COLLECTION}"
        self.token = token
        self.ttl = ttl

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
            with urllib.request.urlopen(req, timeout=15) as r:
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
        """Create the collection and its fields if d9 does not have them yet."""
        status, _ = self._raw("GET", f"/collections/{COLLECTION}")
        if status == 200:
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
                log.warning("%s exists but the token cannot read its schema", COLLECTION)
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
        log.info("created %s with %d fields", COLLECTION, len(FIELDS))
        return True

    # -- cache ----------------------------------------------------------

    def get(self, kind, key):
        """Return {'id': int, 'payload': dict} or None."""
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

    def fresh(self, hit):
        if not hit:
            return False
        try:
            age = time.time() - float(hit["payload"].get("fetched_at") or 0)
        except (TypeError, ValueError):
            return False
        return age < self.ttl
