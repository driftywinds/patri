"""Cache on top of the d9 (Directus 9) `patri_cache` collection.

Directus returns a `json` field as a string on SQLite, so payloads are
decoded defensively. Cache failures are logged, never raised: a cold cache
just means one more NTES call.
"""
import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("patri.cache")


class Cache:
    def __init__(self, base_url: str, token: str, ttl: int):
        self.endpoint = base_url.rstrip("/") + "/items/patri_cache"
        self.token = token
        self.ttl = ttl

    def _call(self, method, path="", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.endpoint + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Cloudflare in front of d9 rejects the default urllib agent.
                "User-Agent": "Mozilla/5.0 (compatible; patri/0.1)",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

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
