"""Handler and rate-limit tests for the verified findings (S1, S2, S3)."""
import os
import socket
import threading
import time
import unittest

from server import app


class ClientIpTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TRUSTED_PROXY", None)

    def test_untrusted_ignores_header(self):
        # Before the fix a client-supplied X-Forwarded-For was believed outright.
        self.assertEqual(app.client_ip("10.0.0.1", "9.9.9.9, 1.2.3.4"), "10.0.0.1")

    def test_trusted_uses_rightmost_hop(self):
        os.environ["TRUSTED_PROXY"] = "1"
        self.assertEqual(app.client_ip("10.0.0.1", "9.9.9.9, 1.2.3.4"), "1.2.3.4")

    def test_trusted_rejects_garbage(self):
        os.environ["TRUSTED_PROXY"] = "1"
        self.assertEqual(app.client_ip("10.0.0.1", "not-an-ip"), "10.0.0.1")

    def test_ipv6_rate_key_is_the_subnet(self):
        self.assertEqual(app.rate_key("2001:db8::1"), "2001:db8::/64")
        self.assertEqual(app.rate_key("2001:db8::2"), "2001:db8::/64")
        self.assertEqual(app.rate_key("1.2.3.4"), "1.2.3.4")


class RateLimiterTest(unittest.TestCase):
    def test_blocks_after_limit(self):
        limiter = app.RateLimiter()
        for _ in range(5):
            self.assertTrue(limiter.allow("k", 5, 60))
        self.assertFalse(limiter.allow("k", 5, 60))

    def test_window_expiry(self):
        limiter = app.RateLimiter()
        for _ in range(5):
            limiter.allow("k", 5, 0.05)
        self.assertFalse(limiter.allow("k", 5, 0.05))
        time.sleep(0.07)
        self.assertTrue(limiter.allow("k", 5, 0.05))

    def test_eviction_keeps_active_buckets(self):
        # The old code cleared the whole table at 5k keys, resetting lockouts.
        limiter = app.RateLimiter(max_keys=2)
        limiter.allow("a", 5, 60)
        limiter.allow("b", 5, 60)
        limiter.allow("c", 5, 60)  # evicts "a", the least recently used
        for _ in range(4):
            self.assertTrue(limiter.allow("b", 5, 60))
        self.assertFalse(limiter.allow("b", 5, 60))


class HandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app._api_limiter = app.RateLimiter()
        app._login_limiter = app.RateLimiter()
        cls.server = app.Server(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, raw):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            sock.sendall(raw)
            out = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                out += chunk
        finally:
            sock.close()
        return out

    def test_health(self):
        got = self.request(b"GET /api/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertTrue(got.startswith(b"HTTP/1.1 200"))

    def test_ready_reports_d9_down(self):
        got = self.request(b"GET /api/ready HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertTrue(got.startswith(b"HTTP/1.1 503"))

    def test_negative_content_length_is_rejected_not_hung(self):
        started = time.time()
        got = self.request(
            b"POST /api/settings HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            b"Content-Length: -1\r\nConnection: close\r\n\r\n"
        )
        self.assertTrue(got.startswith(b"HTTP/1.1 400"))
        self.assertLess(time.time() - started, 3)

    def test_non_numeric_content_length(self):
        got = self.request(
            b"POST /api/settings HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            b"Content-Length: abc\r\nConnection: close\r\n\r\n"
        )
        self.assertTrue(got.startswith(b"HTTP/1.1 400"))

    def test_nul_byte_path(self):
        got = self.request(b"GET /a\x00b HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertTrue(got.startswith(b"HTTP/1.1 404"))

    def test_post_requires_json_content_type(self):
        got = self.request(
            b"POST /api/settings HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n"
            b"Connection: close\r\n\r\n{}"
        )
        self.assertTrue(got.startswith(b"HTTP/1.1 403"))

    def test_post_requires_same_origin(self):
        body = b'{"font":"plex"}'
        got = self.request(
            b"POST /api/settings HTTP/1.1\r\nHost: example.test\r\n"
            b"Origin: https://evil.test\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        self.assertTrue(got.startswith(b"HTTP/1.1 403"))


if __name__ == "__main__":
    unittest.main()
