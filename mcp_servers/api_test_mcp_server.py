"""
API Test MCP Server — 接口测试工具集

将 HTTP 接口测试能力封装为 MCP 工具，供执行 Agent（或任意 MCP Client）调用：
- http_request: 发送 HTTP 请求（GET/POST/PUT/DELETE/PATCH）
- assert_status: 断言响应状态码
- assert_json_field: 断言响应 JSON 字段值
- assert_json_contains: 断言 JSON 包含指定路径
- set_base_url / clear_base_url: 管理公共 Base URL
- api_run_case: 一键执行"请求 + 断言"组合用例

运行方式：
    python -m mcp_servers.api_test_mcp_server                    # stdio
    python -m mcp_servers.api_test_mcp_server --transport sse --port 8002

环境变量：
    ITEST_API_BASE_URL: 默认 Base URL
    ITEST_API_TIMEOUT: 默认超时（秒），默认 10
"""

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastmcp import FastMCP


# =============================================================================
# 会话状态
# =============================================================================

_DEFAULT_BASE_URL = os.getenv("ITEST_API_BASE_URL", "")
_DEFAULT_TIMEOUT = float(os.getenv("ITEST_API_TIMEOUT", "10"))
_base_url = _DEFAULT_BASE_URL
_request_log: List[Dict[str, Any]] = []


mcp = FastMCP(
    "itest-api-test",
    instructions=(
        "HTTP 接口自动化测试 MCP Server。"
        "提供请求发送与响应断言工具，供测试用例执行引擎调用。"
    ),
)


def _resolve_url(url: str) -> str:
    """将相对路径拼接到 Base URL"""
    if url.startswith(("http://", "https://")):
        return url
    if not _base_url:
        raise ValueError(
            f"相对路径 {url} 需要先调用 set_base_url 设置 Base URL"
        )
    return _base_url.rstrip("/") + "/" + url.lstrip("/")


def _log_call(name: str, params: Dict, result: Dict, duration_ms: float) -> None:
    """记录工具调用日志"""
    _request_log.append(
        {
            "tool": name,
            "params": params,
            "result": result,
            "duration_ms": round(duration_ms, 1),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )


# =============================================================================
# 工具实现
# =============================================================================


@mcp.tool()
def http_request(
    method: str = "GET",
    url: str = "",
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[str] = None,
    timeout: Optional[float] = None,
    follow_redirects: bool = True,
) -> dict:
    """
    发送 HTTP 请求并返回状态码、响应体（JSON 自动解析）与耗时。

    Args:
        method: GET / POST / PUT / DELETE / PATCH / HEAD / OPTIONS
        url: 完整 URL 或相对 Base URL 的路径
        params: URL 查询参数
        headers: 请求头
        json_body: JSON 请求体（推荐）
        data: 原始字符串请求体
        timeout: 超时秒数，默认 10
        follow_redirects: 是否跟随重定向
    """
    t0 = time.time()
    full_url = _resolve_url(url or "")
    method = method.upper()
    try:
        resp = httpx.request(
            method=method,
            url=full_url,
            params=params,
            headers=headers,
            json=json_body,
            content=data.encode("utf-8") if data else None,
            timeout=timeout or _DEFAULT_TIMEOUT,
            follow_redirects=follow_redirects,
        )
    except httpx.HTTPError as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _log_call("http_request", {"method": method, "url": full_url}, result, (time.time() - t0) * 1000)
        return result

    body: Any = resp.text
    try:
        body = resp.json()
    except Exception:
        pass

    result = {
        "ok": True,
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": body,
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    _log_call("http_request", {"method": method, "url": full_url}, result, (time.time() - t0) * 1000)
    return result


@mcp.tool()
def assert_status(actual: int, expected: int) -> dict:
    """
    断言实际状态码等于期望状态码。

    Args:
        actual: 实际状态码（来自 http_request 返回的 status_code）
        expected: 期望状态码
    """
    passed = int(actual) == int(expected)
    result = {
        "passed": passed,
        "actual": actual,
        "expected": expected,
        "message": "状态码断言通过" if passed else f"状态码不符: 期望 {expected}, 实际 {actual}",
    }
    _log_call("assert_status", {"actual": actual, "expected": expected}, result, 0.0)
    return result


@mcp.tool()
def assert_json_field(
    body: Any,
    field: str,
    expected: Any,
    path_separator: str = ".",
) -> dict:
    """
    断言 JSON 响应中指定字段值等于期望值。

    Args:
        body: 响应体（dict/list/str，自动解析）
        field: 字段路径，如 "data.user.name"，支持 a.b[0].c 形式
        expected: 期望值
        path_separator: 路径分隔符，默认点号
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            pass

    try:
        actual = _get_path(body, field, path_separator)
        missing = False
    except (KeyError, IndexError, TypeError, ValueError):
        actual = None
        missing = True
    passed = (not missing) and (actual == expected)
    result = {
        "passed": passed,
        "field": field,
        "actual": actual,
        "expected": expected,
        "message": (
            f"字段 {field} 断言通过"
            if passed
            else f"字段 {field} 断言失败: "
                 f"{'字段不存在' if missing else f'期望 {expected}, 实际 {actual}'}"
        ),
    }
    _log_call("assert_json_field", {"field": field, "expected": expected}, result, 0.0)
    return result


@mcp.tool()
def assert_json_contains(
    body: Any,
    field: str,
    expected_substring: str,
) -> dict:
    """
    断言 JSON 响应中指定字段的字符串值包含期望子串。
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            pass
    actual = _get_path(body, field)
    if actual is None:
        passed = False
    else:
        passed = expected_substring in str(actual)
    result = {
        "passed": passed,
        "field": field,
        "actual": actual,
        "contains": expected_substring,
        "message": f"字段 {field} 包含断言{'通过' if passed else '失败'}",
    }
    _log_call("assert_json_contains", {"field": field, "expected_substring": expected_substring}, result, 0.0)
    return result


@mcp.tool()
def set_base_url(base_url: str) -> dict:
    """设置公共 Base URL，后续相对路径请求将自动拼接"""
    global _base_url
    _base_url = base_url.rstrip("/")
    return {"base_url": _base_url, "set": True}


@mcp.tool()
def clear_base_url() -> dict:
    """清除公共 Base URL"""
    global _base_url
    _base_url = ""
    return {"base_url": "", "cleared": True}


@mcp.tool()
def api_run_case(
    method: str,
    url: str,
    expected_status: Optional[int] = None,
    expected_json_field: Optional[str] = None,
    expected_json_value: Optional[Any] = None,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> dict:
    """
    一键执行接口用例：发送请求 + 可选断言，返回整体结果。

    Args:
        method: HTTP 方法
        url: 完整 URL 或相对 Base URL 路径
        expected_status: 期望状态码（可选）
        expected_json_field: 期望断言字段路径（可选）
        expected_json_value: 字段期望值（与 expected_json_field 搭配）
        params / headers / json_body: 请求参数
        timeout: 超时秒数
    """
    resp = http_request(
        method=method,
        url=url,
        params=params,
        headers=headers,
        json_body=json_body,
        timeout=timeout,
    )
    if not resp.get("ok"):
        return {"case_passed": False, "step": "http_request", "error": resp.get("error")}

    checks = []
    if expected_status is not None:
        check = assert_status(resp["status_code"], expected_status)
        checks.append({"step": "assert_status", **check})
        if not check["passed"]:
            return {"case_passed": False, "checks": checks, "response": resp}

    if expected_json_field is not None:
        check = assert_json_field(resp.get("body"), expected_json_field, expected_json_value)
        checks.append({"step": "assert_json_field", **check})
        if not check["passed"]:
            return {"case_passed": False, "checks": checks, "response": resp}

    return {"case_passed": True, "checks": checks, "response": resp}


@mcp.tool()
def get_request_log(limit: int = 20) -> dict:
    """查看最近的工具调用日志（可观测性）"""
    return {"total": len(_request_log), "recent": _request_log[-limit:]}


# =============================================================================
# 工具函数
# =============================================================================


def _path_exists(data: Any, path: str, sep: str = ".") -> bool:
    try:
        _get_path(data, path, sep)
        return True
    except (KeyError, IndexError, TypeError):
        return False


def _get_path(data: Any, path: str, sep: str = ".") -> Any:
    """按路径读取嵌套 JSON，支持 a.b[0].c 语法"""
    if not path:
        return data
    # 支持 [0] 下标语法
    path = path.replace("[", sep).replace("]", "")
    current = data
    for part in path.split(sep):
        if part == "":
            continue
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(f"无法按路径 {path} 解析，当前节点类型: {type(current).__name__}")
    return current


# =============================================================================
# 启动入口
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="iTest-Agent API Test MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="SSE/HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8002, help="SSE/HTTP 监听端口")
    args = parser.parse_args()

    kwargs = {"transport": args.transport}
    if args.transport in ("sse", "streamable-http", "http"):
        kwargs["host"] = args.host
        kwargs["port"] = args.port
    mcp.run(**kwargs)


if __name__ == "__main__":
    main()
