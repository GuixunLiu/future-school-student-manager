"""Owner-approved temporary HTTP read-only proxy. No database access or saved credentials."""
import argparse
from collections import OrderedDict
import hashlib
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

MAX_BODY = 16384
MAX_RESPONSE = 4 * 1024 * 1024


def origin(value):
    p = urlsplit(value)
    if p.scheme != "http" or p.path or p.query or p.fragment or p.username or p.password or not p.hostname or p.port is None:
        raise ValueError("An explicit owner-approved HTTP host and port is required")
    ipaddress.IPv4Address(p.hostname)
    if not 1024 <= p.port <= 65535 or value != "http://" + p.netloc.lower():
        raise ValueError("Invalid temporary origin")
    return p.netloc


def backend_ready(data):
    return (isinstance(data, dict) and data.get("status") == "ready" and data.get("version") == "0.5.0" and data.get("mode") == "roster_review"
            and data.get("roster_review_only") is True and data.get("archive_enabled") is True
            and all(data.get(k) is False for k in ("business_ready", "wechat_sending_enabled", "student_sync_enabled", "parent_binding_enabled")))


class Viewer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, address, public_origin, expires_at, *, upstream=("127.0.0.1", 18082), clock=time.time):
        self.authority = origin(public_origin)
        self.public_origin, self.expires_at, self.upstream, self.clock = public_origin, expires_at, upstream, clock
        self.slots = threading.BoundedSemaphore(8)
        self.lock = threading.Lock()
        self.login_counts = OrderedDict()
        self.cached_health = (0, False)
        self.source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Never log a request, username, token or body.

    def ready(self):
        with self.lock:
            stamp, ready = self.cached_health
            if 0 <= self.clock() - stamp < 2:
                return ready
            conn = HTTPConnection(*self.upstream, timeout=5)
            try:
                conn.request("GET", "/health/ready", headers={"Host": self.upstream[0] + ":" + str(self.upstream[1])})
                response = conn.getresponse()
                data = response.read(32769)
                ready = response.status == 200 and len(data) <= 32768 and backend_ready(json.loads(data))
            except (OSError, ValueError, HTTPException):
                ready = False
            finally:
                conn.close()
            self.cached_health = (self.clock(), ready)
            return ready

    def login_allowed(self, address):
        with self.lock:
            now = self.clock()
            stale = [k for k, (start, _) in self.login_counts.items() if now - start >= 60]
            for key in stale:
                del self.login_counts[key]
            start, count = self.login_counts.get(address, (now, 0))
            if count >= 20:
                return False
            if address not in self.login_counts and len(self.login_counts) >= 512:
                return False
            self.login_counts[address] = (start, count + 1)
            return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # Always close: no request pipelining or ambiguous body reuse.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def version_string(self):
        return "FutureSchoolTemporaryViewer"

    def log_message(self, *args):
        pass

    def reply(self, status, payload, headers=None):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
        self.close_connection = True

    def reject(self, status, code):
        self.reply(status, json.dumps({"error": {"code": code}}, separators=(",", ":")).encode(), {"Content-Type": "application/json"})

    def dispatch(self):
        server = self.server
        hosts, origins = self.headers.get_all("Host", []), self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0].lower() != server.authority:
            return self.reject(403, "viewer_host_rejected")
        if origins and (len(origins) != 1 or origins[0] != server.public_origin):
            return self.reject(403, "viewer_origin_rejected")
        if server.clock() >= server.expires_at:
            return self.reject(410, "temporary_viewer_expired")
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            return self.reject(400, "invalid_path")
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/") or "\\" in self.path:
            return self.reject(400, "invalid_path")
        login = parsed.path in {"/v1/auth/login", "/v1/auth/logout"}
        if self.command not in ({"POST"} if login else {"GET", "HEAD"}):
            return self.reject(405, "viewer_read_only")
        if not login and parsed.path != "/viewer/health" and parsed.path not in {"/", "/workspace.js", "/workspace.css", "/health/ready"} and not parsed.path.startswith("/v1/"):
            return self.reject(404, "not_found")
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Encoding") or self.headers.get_all("Expect") or len(lengths) > 1:
            return self.reject(400, "ambiguous_body")
        raw_length = lengths[0] if lengths else "0"
        if not raw_length.isascii() or not raw_length.isdigit() or len(raw_length) > 5 or int(raw_length) > MAX_BODY:
            return self.reject(413, "body_limit")
        length = int(raw_length)
        if not login and length:
            return self.reject(400, "unexpected_body")
        if not server.ready():
            return self.reject(503, "review_backend_not_ready")
        if parsed.path == "/viewer/health":
            self.reply(200, json.dumps({"status": "ready", "viewer": "temporary_http_read_only", "gateway_sha256": server.source_sha256,
                                       "backend_ready": True, "expires_at": server.expires_at, "data_realtime": False,
                                       "wechat_enabled": False}, separators=(",", ":")).encode(), {"Content-Type": "application/json"})
            return
        if login and not server.login_allowed(self.client_address[0]):
            return self.reject(429, "viewer_login_rate_limit")
        empty_logout = parsed.path == "/v1/auth/logout" and length == 0
        if login and not empty_logout and self.headers.get_content_type() != "application/json":
            return self.reject(415, "json_required")
        auth = self.headers.get_all("Authorization", [])
        if len(auth) > 1 or auth and len(auth[0]) > 128:
            return self.reject(400, "invalid_authorization")
        body = self.rfile.read(length) if length else None
        if length and len(body) != length:
            return self.reject(400, "incomplete_body")
        headers = {"Host": server.upstream[0] + ":" + str(server.upstream[1])}
        if auth:
            headers["Authorization"] = auth[0]
        if login:
            headers["Content-Type"] = "application/json"
        conn = HTTPConnection(*server.upstream, timeout=10)
        try:
            conn.request("GET" if self.command == "HEAD" else self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                return self.reject(502, "upstream_response_limit")
            allowed = {"content-type", "content-security-policy", "www-authenticate", "retry-after"}
            outgoing = {k: v for k, v in response.getheaders() if k.lower() in allowed}
            if parsed.path == "/" and response.status == 200:
                payload = payload.replace("名册核对副本 · 余额以平板最新记录为准 · 微信尚未启用".encode(),
                                          "临时HTTP查看 · 名册/余额快照 · 平板尚未实时同步".encode())
            self.reply(response.status, payload, outgoing)
        except (OSError, ValueError, HTTPException):
            self.reject(502, "upstream_unavailable")
        finally:
            conn.close()

    def do_GET(self):
        self.dispatch()

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--expires-at", type=int, required=True)
    parser.add_argument("--temporary-http-confirmed", action="store_true", required=True)
    args = parser.parse_args()
    authority = origin(args.origin)
    port = urlsplit(args.origin).port
    if port != 18083 or not time.time() < args.expires_at <= time.time() + 15 * 86400:
        raise SystemExit("Use port 18083 and an explicit expiry within 15 days")
    with Viewer(("0.0.0.0", port), args.origin, args.expires_at) as server:
        if not server.ready():
            raise SystemExit("Read-only backend not ready; nothing exposed")
        server.serve_forever(poll_interval=0.5)
