"""
执行引擎 — 通过 MCP 协议调用真实测试工具

核心能力：
1. API 用例：连接 API Test MCP Server，调用 http_request / assert_status / assert_json_field
2. UI 用例：连接 Playwright MCP Server，按解析后的动作序列调用浏览器工具
3. 模拟降级：MCP Server 不可用或模式为 simulated 时，标记执行结果为模拟并继续
4. 工具调用日志：记录 tool / params / result / duration / error

用法：
    engine = ExecutionEngine(mode="mcp")
    result = engine.execute_case(test_case_dict)
"""

import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from execution.step_parser import CasePlan, UIAction, parse_case


# =============================================================================
# MCP 工具客户端
# =============================================================================


class McpSession:
    """
    MCP 会话上下文管理器：通过 stdio 启动 MCP Server 并调用工具。

    注意：连接建立与关闭必须在同一个异步任务内完成，
    因此本类设计为 async context manager，整体包裹请求调用。
    """

    def __init__(
        self,
        server_command: str,
        server_args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ):
        self.server_command = server_command
        self.server_args = server_args or []
        self.cwd = cwd
        self._ctx = None
        self._session: Optional[ClientSession] = None

    async def __aenter__(self) -> "McpSession":
        params = StdioServerParameters(
            command=self.server_command,
            args=self.server_args,
            cwd=self.cwd,
        )
        self._ctx = stdio_client(params)
        read, write = await self._ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用 MCP 工具，返回结构化结果"""
        if self._session is None:
            raise RuntimeError("MCP 会话未建立，请先进入上下文")
        result = await self._session.call_tool(name, arguments)
        return _parse_tool_result(result)

    async def list_tools(self) -> List[str]:
        if self._session is None:
            raise RuntimeError("MCP 会话未建立，请先进入上下文")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]

    async def __aexit__(self, exc_type, exc, tb) -> None:
        errors = []
        if self._session is not None:
            try:
                await self._session.__aexit__(exc_type, exc, tb)
            except Exception as e:
                errors.append(e)
            self._session = None
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(exc_type, exc, tb)
            except Exception as e:
                errors.append(e)
            self._ctx = None
        if errors:
            raise RuntimeError(f"MCP 会话关闭异常: {errors}")


def _parse_tool_result(result: Any) -> Dict[str, Any]:
    """将 MCP CallToolResult 解析为可 JSON 序列化字典"""
    texts = []
    structured: Dict[str, Any] = {}
    for content in getattr(result, "content", []) or []:
        ctype = getattr(content, "type", "")
        if ctype == "text":
            text = getattr(content, "text", "")
            texts.append(text)
            try:
                structured.update(json.loads(text))
            except Exception:
                pass
        elif ctype == "json":
            data = getattr(content, "json", None)
            if isinstance(data, dict):
                structured.update(data)

    if structured:
        payload = structured
    elif texts:
        payload = {"text": "\n".join(texts)}
    else:
        payload = {}

    payload.setdefault("is_error", bool(getattr(result, "isError", False)))
    return payload


# =============================================================================
# 执行引擎
# =============================================================================


class ExecutionEngine:
    """
    用例执行引擎

    Args:
        mode: "mcp" 使用真实 MCP 工具；"simulated" 直接模拟（默认）
        python_cmd: Python 可执行文件路径（默认当前解释器）
        api_server_module: API Test MCP Server 模块
        playwright_server_module: Playwright MCP Server 模块
        api_base_url: API 测试的默认 Base URL
        request_timeout: 单次工具调用超时（秒）
    """

    def __init__(
        self,
        mode: str = "simulated",
        python_cmd: Optional[str] = None,
        api_server_module: str = "mcp_servers.api_test_mcp_server",
        playwright_server_module: str = "mcp_servers.playwright_mcp_server",
        api_base_url: str = "",
        request_timeout: float = 30.0,
    ):
        self.mode = (mode or os.getenv("ITEST_EXECUTION_MODE", "simulated")).lower()
        self.python_cmd = python_cmd or sys.executable
        self.api_server_module = api_server_module
        self.playwright_server_module = playwright_server_module
        self.api_base_url = api_base_url or os.getenv("ITEST_API_BASE_URL", "")
        self.request_timeout = request_timeout
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.tool_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 对外主入口（同步）
    # ------------------------------------------------------------------

    def execute_case(self, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行单条用例（同步接口，供 LangGraph 节点调用）

        Returns:
            dict: {
                case_id, title, status(passed/failed/blocked),
                duration_ms, mode(实际执行模式), details, error?
            }
        """
        case_id = test_case.get("case_id", "unknown")
        title = test_case.get("title", "untitled")
        plan = parse_case(test_case)
        t0 = time.time()

        # 模式 1：模拟执行
        if self.mode == "simulated":
            result = self._simulated_execute(plan, test_case)
            result.update(
                {
                    "case_id": case_id,
                    "title": title,
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "mode": "simulated",
                }
            )
            return result

        # 模式 2：MCP 真实执行（失败则降级模拟）
        try:
            result = asyncio.run(self._mcp_execute(plan, test_case))
            result.update(
                {
                    "case_id": case_id,
                    "title": title,
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "mode": "mcp",
                }
            )
            return result
        except Exception as e:
            self._log_tool(
                "mcp_connect",
                {"case_id": case_id, "plan_kind": plan.kind},
                {"error": f"{type(e).__name__}: {e}"},
                0.0,
            )
            fallback = self._simulated_execute(plan, test_case)
            fallback.update(
                {
                    "case_id": case_id,
                    "title": title,
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "mode": "simulated_fallback",
                    "fallback_reason": f"{type(e).__name__}: {e}",
                }
            )
            return fallback

    # ------------------------------------------------------------------
    # MCP 执行（异步）
    # ------------------------------------------------------------------

    async def _mcp_execute(
        self, plan: CasePlan, test_case: Dict[str, Any]
    ) -> Dict[str, Any]:
        if plan.kind == "api":
            return await self._execute_api_case(plan)
        if plan.kind == "ui":
            return await self._execute_ui_case(plan)
        return self._simulated_execute(plan, test_case)

    async def _execute_api_case(self, plan: CasePlan) -> Dict[str, Any]:
        async with self._api_session() as client:
            if self.api_base_url and plan.url and not plan.url.startswith("http"):
                await client.call_tool(
                    "set_base_url", {"base_url": self.api_base_url}
                )

            params = {
                "method": plan.method,
                "url": plan.url,
                "params": plan.params,
                "headers": plan.headers,
                "json_body": plan.json_body,
            }
            resp = await self._call_with_timeout(client, "http_request", params)
            if not resp.get("ok", False):
                raise RuntimeError(f"HTTP 请求失败: {resp.get('error', resp)}")

            checks = []
            status_ok = True
            if plan.expected_status is not None:
                check = await self._call_with_timeout(
                    client,
                    "assert_status",
                    {"actual": resp.get("status_code"), "expected": plan.expected_status},
                )
                checks.append({"step": "assert_status", **check})
                status_ok = bool(check.get("passed", False))

            field_ok = True
            if plan.expected_json_field is not None:
                check = await self._call_with_timeout(
                    client,
                    "assert_json_field",
                    {
                        "body": resp.get("body"),
                        "field": plan.expected_json_field,
                        "expected": plan.expected_json_value,
                    },
                )
                checks.append({"step": "assert_json_field", **check})
                field_ok = bool(check.get("passed", False))

            return {
                "status": "passed" if (status_ok and field_ok) else "failed",
                "details": {
                    "request": {
                        "method": plan.method,
                        "url": plan.url,
                        "params": plan.params,
                        "json": plan.json_body,
                    },
                    "response": {
                        "status_code": resp.get("status_code"),
                        "body": resp.get("body"),
                    },
                    "checks": checks,
                },
            }

    async def _execute_ui_case(self, plan: CasePlan) -> Dict[str, Any]:
        async with self._browser_session() as client:
            actions_log: List[Dict[str, Any]] = []
            assertions = []

            for action in plan.ui_actions:
                if action.action == "simulate":
                    actions_log.append(
                        {"action": action.action, "note": "无法映射到浏览器操作"}
                    )
                    continue
                result = await self._run_ui_action(client, action)
                actions_log.append(result)
                if action.action == "assert_text":
                    actual = (result.get("text") or "").strip()
                    expected = (action.expected_text or "").strip()
                    passed = bool(expected and expected in actual)
                    assertions.append(
                        {
                            "selector": action.selector,
                            "expected": expected,
                            "actual": actual,
                            "passed": passed,
                        }
                    )

            all_assertions_passed = all(a["passed"] for a in assertions)
            return {
                "status": "passed" if all_assertions_passed else "failed",
                "details": {
                    "actions": actions_log,
                    "assertions": assertions,
                },
            }

    async def _run_ui_action(
        self, client: McpSession, action: UIAction
    ) -> Dict[str, Any]:
        """将 UIAction 映射为 Playwright MCP 工具调用"""
        mapping = {
            "navigate": ("browser_navigate", {"url": action.value}),
            "click": ("browser_click", {"selector": action.selector}),
            "fill": (
                "browser_fill",
                {"selector": action.selector, "value": action.value},
            ),
            "assert_text": (
                "browser_get_text",
                {"selector": action.selector, "timeout_ms": action.timeout_ms},
            ),
            "wait": (
                "browser_wait_for_selector",
                {"selector": action.selector, "timeout_ms": action.timeout_ms},
            ),
            "screenshot": ("browser_screenshot", {}),
        }
        tool_name, args = mapping[action.action]
        result = await self._call_with_timeout(client, tool_name, args)
        result["action"] = action.action
        return result

    async def _call_with_timeout(
        self, client: McpSession, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """带超时与日志的工具调用"""
        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, args), timeout=self.request_timeout
            )
            self._log_tool(tool_name, args, result, (time.time() - t0) * 1000)
            return result
        except asyncio.TimeoutError:
            raise RuntimeError(f"工具 {tool_name} 调用超时（>{self.request_timeout}s）")

    # ------------------------------------------------------------------
    # 模拟执行
    # ------------------------------------------------------------------

    def _simulated_execute(
        self, plan: CasePlan, test_case: Dict[str, Any]
    ) -> Dict[str, Any]:
        """模拟执行：不调用真实工具，标记结果并保留计划信息"""
        notes = list(plan.notes)
        if plan.kind == "api":
            notes.append(
                f"模拟请求 {plan.method} {plan.url or '(未解析)'}"
            )
        elif plan.kind == "ui":
            notes.append(
                f"模拟执行 {len(plan.ui_actions)} 个 UI 动作"
            )
        else:
            notes.append("模拟执行（无可用计划）")

        return {
            "status": "passed",
            "details": {
                "kind": plan.kind,
                "simulated": True,
                "notes": notes,
            },
        }

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def _api_session(self) -> McpSession:
        return McpSession(
            server_command=self.python_cmd,
            server_args=["-m", self.api_server_module],
            cwd=self._project_root,
        )

    def _browser_session(self) -> McpSession:
        return McpSession(
            server_command=self.python_cmd,
            server_args=["-m", self.playwright_server_module],
            cwd=self._project_root,
        )

    def _log_tool(
        self,
        tool: str,
        params: Dict[str, Any],
        result: Dict[str, Any],
        duration_ms: float,
    ) -> None:
        self.tool_log.append(
            {
                "tool": tool,
                "params": _truncate(params),
                "result": _truncate(result),
                "duration_ms": round(duration_ms, 1),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

    def export_tool_log(self) -> Dict[str, Any]:
        """导出工具调用日志（可观测性）"""
        return {
            "mode": self.mode,
            "tool_calls": self.tool_log,
            "total_calls": len(self.tool_log),
        }


def _truncate(data: Any, max_len: int = 2000) -> Any:
    """限制日志字段大小，防止大响应体撑爆日志"""
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    if len(text) > max_len:
        return text[:max_len] + f"...(truncated {len(text) - max_len} chars)"
    return data
