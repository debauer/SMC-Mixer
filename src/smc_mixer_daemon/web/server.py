"""Local HTTP server: static page plus JSON API, with browser-origin protection for writes."""

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import ConfigError, WebConfig
from smc_mixer_daemon.web.api import Api, NotFoundError
from smc_mixer_daemon.web.auth import AuthError

log = logging.getLogger(__name__)

PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/dom.js": ("dom.js", "text/javascript; charset=utf-8"),
    "/buttons.js": ("buttons.js", "text/javascript; charset=utf-8"),
    "/button_editor.js": ("button_editor.js", "text/javascript; charset=utf-8"),
    "/knobs.js": ("knobs.js", "text/javascript; charset=utf-8"),
    "/knob_editor.js": ("knob_editor.js", "text/javascript; charset=utf-8"),
    "/mqtt_editor.js": ("mqtt_editor.js", "text/javascript; charset=utf-8"),
    "/strips.js": ("strips.js", "text/javascript; charset=utf-8"),
    "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 64 * 1024


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, web: WebConfig, api: Api) -> None:
        self.api = api
        self.loopback = web.host in LOOPBACK
        super().__init__((web.host, web.port), Handler)

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        return {f"{name}:{port}" for name in ("127.0.0.1", "localhost")} | {f"[::1]:{port}"}


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), format % args)

    def reply(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status: HTTPStatus, data: Any) -> None:
        self.reply(status, json.dumps(data).encode(), "application/json")

    def error(self, status: HTTPStatus, message: str) -> None:
        self.reply_json(status, {"error": message})

    def trusted(self) -> bool:
        """Reject DNS-rebinding (foreign Host on loopback) and cross-site browser requests."""
        host = self.headers.get("Host", "")
        if self.server.loopback and host not in self.server.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlsplit(origin).netloc == host

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self.trusted():
            return self.error(HTTPStatus.FORBIDDEN, "untrusted host or origin")
        if path in PAGES:
            name, content_type = PAGES[path]
            body = (resources.files("smc_mixer_daemon.web") / "static" / name).read_bytes()
            return self.reply(HTTPStatus.OK, body, content_type)
        self.api_call(lambda: self.server.api.get(path))

    def do_PUT(self) -> None:
        path = urlsplit(self.path).path
        if not self.trusted() or self.headers.get_content_type() != "application/json":
            return self.error(HTTPStatus.FORBIDDEN, "untrusted origin or non-JSON request")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
        if not 0 <= length <= MAX_BODY:
            return self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError as exc:
            return self.error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
        token = self.headers.get("X-SMC-Token")
        self.api_call(lambda: self.server.api.put(path, data, token))

    def api_call(self, call: Any) -> None:
        try:
            self.reply_json(HTTPStatus.OK, call())
        except NotFoundError:
            self.error(HTTPStatus.NOT_FOUND, "not found")
        except AuthError as exc:
            self.error(HTTPStatus.FORBIDDEN, str(exc))
        except ConfigError as exc:
            self.error(HTTPStatus.BAD_REQUEST, str(exc))
        except (BackendError, OSError) as exc:
            self.error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))


def start(web: WebConfig, api: Api) -> tuple[Server, threading.Thread]:
    server = Server(web, api)
    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    log.info("web interface on http://%s:%s/", host, port)
    if not server.loopback:
        log.warning("web interface is bound to %s without authentication; anyone reaching it can edit the config", host)
    return server, thread
