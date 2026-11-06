import unittest
from unittest.mock import patch

from wx_watcher.infrastructure.http_server import LoopbackHTTPServer


class LoopbackServerUnitTests(unittest.TestCase):
    def test_binding_uses_the_assigned_numeric_address_without_reverse_dns(self):
        server = LoopbackHTTPServer.__new__(LoopbackHTTPServer)
        server.server_address = ("127.0.0.1", 43210)
        with (
            patch(
                "wx_watcher.infrastructure.http_server.TCPServer.server_bind"
            ) as bind,
            patch("socket.getfqdn", side_effect=AssertionError("reverse DNS")),
        ):
            server.server_bind()
        bind.assert_called_once_with(server)
        self.assertEqual(server.server_name, "127.0.0.1")
        self.assertEqual(server.server_port, 43210)
