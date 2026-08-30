import logging
import json

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QTcpServer, QHostAddress, QHttpHeaders
from PySide6.QtHttpServer import (
    QHttpServer,
    QHttpServerRequest,
    QHttpServerResponse,
)

from qt_ui import settings

logger = logging.getLogger('restim.http')

# PySide6 6.11 cannot reliably return QHttpServerResponse / dict / bytes from
# Python route handlers (Shiboken copy-convert fails → empty body). Returning a
# JSON *string* works and still runs addAfterRequestHandler for CORS.


def json_response(payload, status=None):
    """Serialize payload as JSON text for QHttpServer route handlers."""
    return json.dumps(payload)


def empty_response(status=None):
    """Empty body (used for OPTIONS preflight)."""
    return ''


# Kept for callers that branch on method-not-allowed etc. Status codes are not
# honored when returning strings; use payload flags instead.
class StatusCode:
    Ok = 200
    BadRequest = 400
    MethodNotAllowed = 405
    NoContent = 204


def _apply_cors_headers(response: QHttpServerResponse):
    headers = response.headers()
    headers.append(QHttpHeaders.WellKnownHeader.AccessControlAllowOrigin, '*')
    headers.append(QHttpHeaders.WellKnownHeader.AccessControlAllowMethods, 'GET, POST, OPTIONS')
    headers.append(QHttpHeaders.WellKnownHeader.AccessControlAllowHeaders, 'Content-Type')
    headers.append(QHttpHeaders.WellKnownHeader.AccessControlMaxAge, '86400')
    response.setHeaders(headers)


class HttpServer(QObject):
    def __init__(self, parent):
        super().__init__(parent)

        self.http_server = QHttpServer(self)
        self.tcp_server = QTcpServer(self)
        self.bottomless_pit = []
        self.actions = []
        self._listening = False

        enabled = settings.rest_enabled.get()
        port = settings.rest_port.get()
        localhost_only = settings.rest_localhost_only.get()

        if not enabled:
            logger.info("REST API not enabled.")
            return

        address = QHostAddress.SpecialAddress.LocalHost if localhost_only else QHostAddress.SpecialAddress.Any

        if not self.tcp_server.listen(address, port):
            logger.error("HTTP server failed to listen on port")
            logger.error(f"{self.tcp_server.errorString()}")
            return

        if not self.http_server.bind(self.tcp_server):
            logger.error("HTTP server failed to bind to socket")
            return

        self._listening = True
        bind_desc = "localhost" if localhost_only else "0.0.0.0 (LAN)"
        logger.info(f"HTTP server active at {bind_desc}:{port}")

        self.http_server.addAfterRequestHandler(self.http_server, self._after_request)
        self.bottomless_pit.append(self._after_request)

        self.route("/v1/actions", self.api_actions)

    @property
    def listening(self):
        return self._listening

    def _after_request(self, request: QHttpServerRequest, response: QHttpServerResponse):
        _apply_cors_headers(response)

    def add_action(self, action_string, fn):
        self.actions.append(action_string)
        self.route(f"/v1/actions/{action_string}", fn)

    def route(self, rule, fn):
        # store fn somewhere, because QHttpServer::route
        # forgets to increase the object refcount
        wrapped = self._wrap_with_options(fn)
        self.bottomless_pit.append(wrapped)
        self.http_server.route(rule, wrapped)

    def _wrap_with_options(self, fn):
        def handler(request: QHttpServerRequest):
            if request.method() == QHttpServerRequest.Method.Options:
                return empty_response()
            return fn(request)

        return handler

    def api_actions(self, request: QHttpServerRequest):
        if request.method() != QHttpServerRequest.Method.Get:
            return json_response({'ok': False, 'errors': ['method not allowed']})
        return json_response({'actions': self.actions})
