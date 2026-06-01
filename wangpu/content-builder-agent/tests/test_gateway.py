from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from content_builder.server.gateway import app

REAL_ASYNC_CLIENT = httpx.AsyncClient


class GatewayStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {"CONTENT_BUILDER_PAIRING_TOKEN": "phone-token"})
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.environment.stop()

    def test_gateway_status_rejects_invalid_token_without_contacting_upstream(self) -> None:
        with patch("content_builder.server.gateway.httpx.AsyncClient") as async_client:
            response = self.client.get("/api/content-builder/gateway/status", headers={"x-api-key": "wrong"})

        self.assertEqual(response.json(), {"paired": False, "agent_server_ready": False})
        async_client.assert_not_called()

    def test_gateway_status_waits_for_the_loopback_agent_server(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"paired": True}, request=request)
        )

        def client_factory(*_args, **_kwargs):
            return REAL_ASYNC_CLIENT(transport=transport)

        with patch("content_builder.server.gateway.httpx.AsyncClient", side_effect=client_factory):
            response = self.client.get("/api/content-builder/gateway/status", headers={"x-api-key": "phone-token"})

        self.assertEqual(response.json(), {"paired": True, "agent_server_ready": True})


if __name__ == "__main__":
    unittest.main()
