"""Tests for payload validation and limit parameter enforcement.

Covers:
- Empty/whitespace-only payloads rejected (issue #51)
- Negative and zero limits rejected (issue #52)
"""

from __future__ import annotations

import pytest

from piazza import PiazzaClient
from piazza.transport_http import PiazzaAPIError


class TestPayloadValidation:
    """Reject empty and whitespace-only payloads."""

    def test_empty_payload_rejected(self, server_url: str) -> None:
        client = PiazzaClient(server_url, "tester")
        with pytest.raises(PiazzaAPIError) as exc_info:
            client.channel_send("test-payload", "")
        assert exc_info.value.status_code == 400
        assert (
            "non-empty" in str(exc_info.value).lower() or "payload" in str(exc_info.value).lower()
        )
        client.close()

    def test_whitespace_only_payload_rejected(self, server_url: str) -> None:
        client = PiazzaClient(server_url, "tester")
        with pytest.raises(PiazzaAPIError) as exc_info:
            client.channel_send("test-payload", "   ")
        assert exc_info.value.status_code == 400
        client.close()

    def test_valid_payload_accepted(self, server_url: str) -> None:
        client = PiazzaClient(server_url, "tester")
        client.channel_send("test-payload", "hello world")
        msgs = client.channel_read("test-payload")
        assert len(msgs) == 1
        assert msgs[0].payload == "hello world"
        client.close()

    def test_whitespace_padded_payload_accepted(self, server_url: str) -> None:
        """Payload with leading/trailing whitespace but non-empty content should be accepted."""
        client = PiazzaClient(server_url, "tester")
        client.channel_send("test-payload-ws", "  hello  ")
        msgs = client.channel_read("test-payload-ws")
        assert len(msgs) == 1
        # Payload should preserve whitespace in content (we only reject all-whitespace)
        assert msgs[0].payload == "  hello  "
        client.close()


class TestLimitValidation:
    """Validate limit parameter in query/channel_read."""

    def test_negative_limit_rejected(self, server_url: str) -> None:
        from piazza._vendor.httpclient import Client as HttpClient

        http = HttpClient()
        resp = http.get(f"{server_url}/v1/query?channel=test-limit&limit=-1")
        assert resp.status_code == 400
        body = resp.json()
        assert "limit" in body.get("message", "").lower()
        http.close()

    def test_zero_limit_rejected(self, server_url: str) -> None:
        from piazza._vendor.httpclient import Client as HttpClient

        http = HttpClient()
        resp = http.get(f"{server_url}/v1/query?channel=test-limit&limit=0")
        assert resp.status_code == 400
        http.close()

    def test_positive_limit_accepted(self, server_url: str) -> None:
        client = PiazzaClient(server_url, "tester")
        client.channel_send("test-limit", "msg1")
        client.channel_send("test-limit", "msg2")
        msgs = client.channel_read("test-limit", limit=1)
        assert len(msgs) == 1
        client.close()

    def test_non_integer_limit_rejected(self, server_url: str) -> None:
        from piazza._vendor.httpclient import Client as HttpClient

        http = HttpClient()
        resp = http.get(f"{server_url}/v1/query?channel=test-limit&limit=abc")
        assert resp.status_code == 400
        body = resp.json()
        assert "integer" in body.get("message", "").lower()
        http.close()
