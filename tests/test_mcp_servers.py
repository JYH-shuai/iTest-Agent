"""
自研 MCP Server 单元测试
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mcp_servers.api_test_mcp_server import (
    assert_json_contains,
    assert_json_field,
    assert_status,
    clear_base_url,
    http_request,
    mcp as api_mcp,
    set_base_url,
)
from mcp_servers.playwright_mcp_server import mcp as pw_mcp


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({"data": {"name": "itest", "count": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    srv = HTTPServer(("127.0.0.1", 18771), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


class TestToolRegistration:
    def test_api_server_tools(self):
        tools = asyncio.run(api_mcp.list_tools())
        names = [t.name for t in tools]
        assert "http_request" in names
        assert "assert_status" in names
        assert "assert_json_field" in names
        assert "set_base_url" in names

    def test_playwright_server_tools(self):
        tools = asyncio.run(pw_mcp.list_tools())
        names = [t.name for t in tools]
        assert "browser_navigate" in names
        assert "browser_click" in names
        assert "browser_fill" in names
        assert "browser_screenshot" in names


class TestHttpRequestTool:
    def test_get_json(self, http_server):
        resp = http_request(
            method="GET",
            url=f"http://127.0.0.1:{http_server.server_port}/api/items",
        )
        assert resp["ok"] is True
        assert resp["status_code"] == 200
        assert resp["body"]["data"]["name"] == "itest"

    def test_relative_url_with_base_url(self, http_server):
        set_base_url(f"http://127.0.0.1:{http_server.server_port}")
        try:
            resp = http_request(method="GET", url="/api/items")
            assert resp["ok"] is True
        finally:
            clear_base_url()


class TestAssertTools:
    def test_assert_status_pass(self):
        result = assert_status(200, 200)
        assert result["passed"] is True

    def test_assert_status_fail(self):
        result = assert_status(500, 200)
        assert result["passed"] is False

    def test_assert_json_field_pass(self):
        result = assert_json_field(
            {"data": {"name": "itest"}}, "data.name", "itest"
        )
        assert result["passed"] is True

    def test_assert_json_field_missing(self):
        result = assert_json_field({"data": {}}, "data.name", "itest")
        assert result["passed"] is False

    def test_assert_json_field_list_index(self):
        result = assert_json_field(
            {"items": [{"id": 1}, {"id": 2}]}, "items[1].id", 2
        )
        assert result["passed"] is True

    def test_assert_json_contains(self):
        result = assert_json_contains(
            {"data": {"name": "itest-agent"}}, "data.name", "agent"
        )
        assert result["passed"] is True
