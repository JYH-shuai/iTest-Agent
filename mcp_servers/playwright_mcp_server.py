"""
Playwright MCP Server — 浏览器 UI 测试工具集

将浏览器操作封装为 MCP 工具，供执行 Agent（或任意 MCP Client）调用：
- browser_navigate: 打开指定 URL
- browser_click: 点击指定选择器元素
- browser_fill: 向输入框填写文本
- browser_get_text: 读取元素文本（用于断言）
- browser_screenshot: 截图保存
- browser_wait_for_selector: 等待元素出现
- browser_title / browser_url: 读取页面状态
- browser_new_page / browser_close: 页面生命周期管理
- browser_check: 环境自检（浏览器是否可用）

运行方式：
    python -m mcp_servers.playwright_mcp_server                    # stdio
    python -m mcp_servers.playwright_mcp_server --transport sse --port 8001

环境变量：
    PLAYWRIGHT_HEADLESS: true/false，默认 true（Docker/CI 环境）
    PLAYWRIGHT_TIMEOUT_MS: 默认 10000
"""

import argparse
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastmcp import FastMCP


# =============================================================================
# 浏览器会话管理（单例，惰性初始化）
# =============================================================================


class BrowserSession:
    """
    浏览器会话单例

    - 首次调用工具时惰性启动 Chromium
    - 使用 sync Playwright API（FastMCP 会将同步工具放到线程池执行）
    - 会话级共享浏览器上下文，减少重复启动开销
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def headless(self) -> bool:
        return os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() in ("1", "true", "yes")

    @property
    def timeout_ms(self) -> int:
        return int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "10000"))

    def ensure_ready(self) -> None:
        """确保 Playwright 与浏览器已就绪（惰性初始化）"""
        if self._page is not None and not self._page.is_closed():
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright 未安装，请先执行: pip install playwright && "
                "playwright install chromium"
            ) from e

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self.headless)
        except Exception as e:
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"Chromium 启动失败（{e}）。请确认已安装浏览器: "
                f"playwright install chromium"
            ) from e

        self._context = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def close(self) -> None:
        """关闭浏览器并释放资源"""
        for target in (self._page, self._context, self._browser, self._playwright):
            try:
                if target is not None and hasattr(target, "close"):
                    target.close()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    @property
    def page(self):
        self.ensure_ready()
        return self._page


_session = BrowserSession()


# =============================================================================
# 工具实现
# =============================================================================

mcp = FastMCP(
    "itest-playwright",
    instructions=(
        "浏览器 UI 自动化测试 MCP Server。"
        "提供导航、点击、填表、文本读取、截图、等待等工具，"
        "供测试用例执行引擎调用。"
    ),
)


@mcp.tool()
def browser_check() -> dict:
    """环境自检：返回 Playwright / Chromium 是否可用"""
    try:
        _session.ensure_ready()
        return {"ok": True, "headless": _session.headless, "browser": "chromium"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def browser_navigate(url: str, wait_until: str = "load") -> dict:
    """
    打开指定 URL 并等待页面加载完成。

    Args:
        url: 页面地址（http/https）
        wait_until: 等待时机，可选 load / domcontentloaded / networkidle
    """
    page = _session.page
    t0 = time.time()
    page.goto(url, wait_until=wait_until)
    return {
        "url": page.url,
        "title": page.title(),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }


@mcp.tool()
def browser_new_page(url: Optional[str] = None) -> dict:
    """新建一个空白页面（可选指定 URL），并切换为当前活动页"""
    _session.ensure_ready()
    page = _session._context.new_page()
    page.set_default_timeout(_session.timeout_ms)
    _session._page = page
    if url:
        page.goto(url)
    return {"url": page.url, "title": page.title()}


@mcp.tool()
def browser_click(
    selector: str,
    timeout_ms: Optional[int] = None,
    click_count: int = 1,
) -> dict:
    """
    点击指定 CSS 选择器对应的元素。

    Args:
        selector: CSS 选择器，如 #login-btn / input[name="submit"]
        timeout_ms: 等待元素出现的超时（毫秒），默认取环境变量 PLAYWRIGHT_TIMEOUT_MS
        click_count: 点击次数（默认 1；2 表示双击）
    """
    page = _session.page
    t0 = time.time()
    page.click(selector, timeout=timeout_ms or _session.timeout_ms, click_count=click_count)
    return {
        "selector": selector,
        "clicked": True,
        "url": page.url,
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }


@mcp.tool()
def browser_fill(selector: str, value: str, clear_first: bool = True) -> dict:
    """
    向输入框填写文本。

    Args:
        selector: CSS 选择器
        value: 要填入的文本
        clear_first: 填写前是否清空原有内容
    """
    page = _session.page
    if clear_first:
        page.fill(selector, value)
    else:
        page.type(selector, value)
    return {
        "selector": selector,
        "value_length": len(value),
        "filled": True,
    }


@mcp.tool()
def browser_get_text(selector: str, timeout_ms: Optional[int] = None) -> dict:
    """
    读取元素文本内容，用于断言页面展示。

    Args:
        selector: CSS 选择器
        timeout_ms: 等待超时（毫秒）
    """
    page = _session.page
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=timeout_ms or _session.timeout_ms)
    return {"selector": selector, "text": locator.inner_text()}


@mcp.tool()
def browser_get_attribute(selector: str, attribute: str) -> dict:
    """读取元素属性值（如 href、class、value）"""
    page = _session.page
    locator = page.locator(selector).first
    locator.wait_for(state="attached", timeout=_session.timeout_ms)
    return {
        "selector": selector,
        "attribute": attribute,
        "value": locator.get_attribute(attribute),
    }


@mcp.tool()
def browser_screenshot(path: Optional[str] = None) -> dict:
    """
    对当前页面截图。

    Args:
        path: 保存路径；为空时自动生成 timestamp 命名文件

    Returns:
        截图文件路径
    """
    page = _session.page
    if not path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = f"screenshots/screenshot_{ts}.png"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    page.screenshot(path=path, full_page=True)
    return {"path": path, "url": page.url}


@mcp.tool()
def browser_wait_for_selector(
    selector: str,
    state: str = "visible",
    timeout_ms: Optional[int] = None,
) -> dict:
    """
    等待指定元素出现。

    Args:
        selector: CSS 选择器
        state: 元素状态，visible / attached / hidden / detached
        timeout_ms: 超时（毫秒）
    """
    page = _session.page
    t0 = time.time()
    page.wait_for_selector(
        selector,
        state=state,
        timeout=timeout_ms or _session.timeout_ms,
    )
    return {
        "selector": selector,
        "state": state,
        "found": True,
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }


@mcp.tool()
def browser_title() -> dict:
    """读取当前页面标题"""
    page = _session.page
    return {"title": page.title(), "url": page.url}


@mcp.tool()
def browser_url() -> dict:
    """读取当前页面 URL"""
    page = _session.page
    return {"url": page.url}


@mcp.tool()
def browser_close() -> dict:
    """关闭浏览器会话，释放资源"""
    _session.close()
    return {"closed": True}


# =============================================================================
# 启动入口
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="iTest-Agent Playwright MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="SSE/HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8001, help="SSE/HTTP 监听端口")
    args = parser.parse_args()

    kwargs = {"transport": args.transport}
    if args.transport in ("sse", "streamable-http", "http"):
        kwargs["host"] = args.host
        kwargs["port"] = args.port
    mcp.run(**kwargs)


if __name__ == "__main__":
    main()
