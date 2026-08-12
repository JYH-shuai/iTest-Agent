"""
执行引擎单元/集成测试

- 模拟执行模式
- MCP 真实执行（本地 HTTP 服务）
- 断言失败标记 failed
- MCP 不可用时降级 simulated_fallback
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from execution.engine import ExecutionEngine


class _EchoHandler(BaseHTTPRequestHandler):
    """测试用 HTTP 服务：POST 返回 code=0"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        payload = json.dumps(
            {"code": 0, "echo": json.loads(body) if body else None},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    srv = HTTPServer(("127.0.0.1", 18770), _EchoHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _api_case(url, expected_code=0, expected_status=200):
    return {
        "case_id": "TC-ENG-001",
        "title": "登录接口",
        "type": "接口测试",
        "test_data": {
            "method": "POST",
            "url": url,
            "json": {"username": "admin", "password": "x"},
            "expected_status": expected_status,
            "expected_json_field": "code",
            "expected_json_value": expected_code,
        },
        "steps": [{"step": 1, "action": "POST /api/login", "expected": "返回 200"}],
    }


class TestSimulatedMode:
    def test_simulated_api_passes(self):
        engine = ExecutionEngine(mode="simulated")
        result = engine.execute_case(_api_case("http://127.0.0.1:18770/api/login"))
        assert result["status"] == "passed"
        assert result["mode"] == "simulated"
        assert result["details"]["simulated"] is True

    def test_simulated_ui_passes(self):
        case = {
            "case_id": "TC-ENG-002",
            "title": "登录 UI",
            "type": "功能测试",
            "test_data": {"url": "http://example.com"},
            "steps": [{"step": 1, "action": "打开 http://example.com", "expected": ""}],
        }
        result = ExecutionEngine(mode="simulated").execute_case(case)
        assert result["status"] == "passed"
        assert result["mode"] == "simulated"


class TestMcpMode:
    def test_mcp_api_passes(self, http_server):
        engine = ExecutionEngine(mode="mcp", request_timeout=30)
        result = engine.execute_case(
            _api_case(f"http://127.0.0.1:{http_server.server_port}/api/login")
        )
        assert result["mode"] == "mcp"
        assert result["status"] == "passed"
        assert len(engine.tool_log) >= 2
        # 工具调用日志包含 http_request 与断言
        tools = [log["tool"] for log in engine.tool_log]
        assert "http_request" in tools
        assert "assert_status" in tools
        assert "assert_json_field" in tools

    def test_mcp_api_failed_assertion(self, http_server):
        engine = ExecutionEngine(mode="mcp", request_timeout=30)
        result = engine.execute_case(
            _api_case(
                f"http://127.0.0.1:{http_server.server_port}/api/login",
                expected_code=999,  # 实际返回 0，断言失败
            )
        )
        assert result["mode"] == "mcp"
        assert result["status"] == "failed"

    def test_mcp_fallback_when_unreachable(self):
        # 模拟 MCP 工具调用异常（如服务不可达）→ 降级模拟执行
        engine = ExecutionEngine(mode="mcp", request_timeout=10)
        import execution.engine as engine_module

        async def _boom(self, name, arguments):
            raise ConnectionError("MCP Server 不可达")

        original = engine_module.McpSession.call_tool
        engine_module.McpSession.call_tool = _boom
        try:
            result = engine.execute_case(_api_case("http://127.0.0.1:1/api/login"))
        finally:
            engine_module.McpSession.call_tool = original
        assert result["mode"] == "simulated_fallback"
        assert "fallback_reason" in result
        assert result["status"] == "passed"
