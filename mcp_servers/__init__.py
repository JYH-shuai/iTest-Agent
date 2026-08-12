"""
iTest-Agent 自研 MCP Server 包（mcp_servers）

包含：
- playwright_mcp_server: 基于 Playwright 的浏览器 UI 测试工具
- api_test_mcp_server: 基于 HTTPX 的接口测试工具

用法：
    python -m mcp_servers.playwright_mcp_server    # stdio 传输（默认）
    python -m mcp_servers.playwright_mcp_server --transport sse --port 8001
    python -m mcp_servers.api_test_mcp_server       # stdio 传输（默认）
    python -m mcp_servers.api_test_mcp_server --transport sse --port 8002
"""
