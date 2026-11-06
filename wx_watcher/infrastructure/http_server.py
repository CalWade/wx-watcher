"""Numeric loopback listeners with deterministic startup bookkeeping."""

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        # Our authenticated listeners use their numeric address throughout.
        # HTTPServer's reverse hostname lookup is unnecessary for these endpoints.
        self.server_name, self.server_port = self.server_address[:2]
