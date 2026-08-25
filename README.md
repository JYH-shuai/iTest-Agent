# iTest-Agent — 基于 LangGraph 多 Agent 协作的智能测试系统

输入 **PRD 需求文档**，自动完成 **需求分析 → 测试用例生成 → 用例评审 → 用例执行 → 测试报告** 的全流程闭环。通过自研 MCP Server 对接真实测试工具（Playwright 浏览器 UI + HTTP 接口），实现“生成即可执行”。

## 核心特性

| 特性 | 说明 |
|------|------|
| 多 Agent 协作 | 需求分析 / 用例生成 / 用例评审 / 执行 / 报告 五个 Agent，LangGraph 状态机编排 |
| 评审回退闭环 | 用例评审不通过自动回退补充边界/异常用例，迭代收敛（默认上限 3 轮） |
| 双向追溯 | 每条用例关联需求原文段落，需求变更自动定位受影响用例 |
| 增量更新 | 需求变更时仅重生成变更功能对应用例 |
| MCP 工具执行 | 自研 Playwright MCP Server 与 API Test MCP Server，标准协议对接真实工具 |
| 模拟降级 | 无浏览器/无 API Key 环境自动降级，保证全流程可演示 |
| Web UI | Gradio 可视化界面：PRD 上传、进度跟踪、用例/报告查看、增量更新、历史任务 |
| 多格式报告 | Markdown / PDF / JSON / Excel |
| 一键部署 | Docker Compose 启动 Agent 服务 + 执行环境 |

## 架构

```
PRD (.md)
   │
   ▼
┌────────────────────────── LangGraph StateGraph ──────────────────────────┐
│ 需求分析Agent ──► 用例生成Agent ──► 用例评审Agent ──(不通过)──► 重新生成    │
│      │                 │                 │（通过）                          │
│      │                 │                 ▼                                │
│      │                 │            执行Agent ──► 报告Agent ──► END        │
└──────┼─────────────────┼─────────────────┼────────────────────────────────┘
       │                 │                 │
       ▼                 ▼                 ▼
  RAG 知识库       用例/追溯矩阵     MCP 工具层
  (Chroma)        (JSON/MD/XLSX)   Playwright / API Test
```

## 目录结构

```
iTest-Agent/
├── api/                        # FastAPI 服务层
│   ├── main.py                 # REST 接口
│   ├── schemas.py              # 请求/响应模型
│   └── task_store.py           # 任务管理与持久化
├── agents/                     # 各 Agent 实现
│   ├── requirement_analyzer.py # 需求分析 Agent（含规则解析降级）
│   ├── report_generator.py     # 报告生成 Agent（Markdown/PDF）
│   └── prompt_templates.py     # Prompt 模板
├── graph/                      # LangGraph 工作流
│   ├── workflow.py             # StateGraph 拓扑
│   ├── nodes.py                # 各节点实现
│   ├── state.py                # AgentState 定义
│   └── checkpoint_sqlite.py    # SQLite Checkpoint
├── execution/                  # 执行引擎
│   ├── engine.py               # MCP 客户端 + 模拟降级
│   └── step_parser.py          # 自然语言步骤 → 可执行计划
├── mcp_servers/                # 自研 MCP Server
│   ├── playwright_mcp_server.py
│   └── api_test_mcp_server.py
├── knowledge_base/             # RAG 知识库（方法论 + 历史用例）
├── models/                     # 数据模型（TestCase/追溯）
├── exporters/                  # Excel / JSON 导出
├── frontend/                   # Gradio Web UI
│   └── app.py                  # 可视化界面（对接 FastAPI）
├── docker/                     # Dockerfile + docker-compose
├── docs/需求分析.md            # 需求分析说明书
└── tests/                      # 单元/集成测试
```

## 快速开始

### 1. 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 构建知识库（可选，Mock 模式会自动降级）
python load_knowledge_base.py

# 启动 API 服务
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 2. Docker Compose 一键部署

```bash
cd docker
cp .env.example .env
docker compose up -d --build
```

启动后：
- API 服务：http://localhost:8000 （Swagger 文档：http://localhost:8000/docs）
- Web UI：http://localhost:7860 （见下文「Web UI」）
- Playwright MCP Server（SSE）：http://localhost:8001
- API Test MCP Server（SSE）：http://localhost:8002

### 2.5 Web UI（Gradio）

```bash
# 先启动 API 服务（端口 8000），再启动 UI
pip install gradio
python frontend/app.py
# 打开 http://127.0.0.1:7860
```

功能：
- 上传 PRD 文件或直接粘贴文本，一键触发全流程
- 参数面板：LLM 模型 / 执行模式（simulated|mcp）/ Mock LLM（无 API Key 离线演示）/ 评审迭代轮数
- 实时进度：任务状态 + 阶段进度条（分析 -> 生成 -> 评审 -> 执行 -> 报告）
- 结果查看：功能点列表、45+ 条用例明细（含步骤/预期）、评审得分、执行统计
- 报告在线预览 + Markdown/PDF/JSON 下载
- 增量更新：勾选变更功能点，仅重生成受影响用例
- 历史任务：最近 20 条任务，点击行回看结果

UI 通过环境变量配置后端地址：`ITEST_API_BASE`（默认 `http://127.0.0.1:8000`）、`ITEST_UI_PORT`（默认 `7860`）。

### 3. 命令行演示完整工作流

```bash
# Mock LLM + 模拟执行（无 API Key 可跑通）
ITEST_MOCK_LLM=1 ITEST_EXECUTION_MODE=simulated \
  python graph/demo.py --prd tests/sample_prd.md

# 真实执行（需安装 Playwright 浏览器 + 配置目标环境）
ITEST_EXECUTION_MODE=mcp ITEST_API_BASE_URL=https://api.example.com \
  python graph/demo.py --prd tests/sample_prd.md
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/pipeline` | 上传 PRD（multipart）并触发全流程，`sync=true` 同步等待 |
| GET | `/api/v1/tasks` | 任务列表 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态与阶段产物摘要 |
| GET | `/api/v1/tasks/{task_id}/report?format=md\|pdf\|json` | 下载测试报告 |
| POST | `/api/v1/tasks/{task_id}/incremental` | 增量更新（`{changed_function_ids}`） |
| GET | `/health` | 健康检查 |

### 调用示例

```bash
# 1. 上传 PRD 并同步执行
curl -X POST http://localhost:8000/api/v1/pipeline \
  -F "file=@tests/sample_prd.md" \
  -F "mock_llm=true" \
  -F "execution_mode=simulated" \
  -F "sync=true"

# 2. 查询任务状态
curl http://localhost:8000/api/v1/tasks/{task_id}

# 3. 下载报告
curl -o report.md http://localhost:8000/api/v1/tasks/{task_id}/report?format=md

# 4. 增量更新（仅重生成变更功能）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/incremental \
  -H "Content-Type: application/json" \
  -d '{"changed_function_ids": ["FUNC-001-01"]}'
```

## MCP Server 用法

```bash
# stdio 模式（执行引擎内部使用）
python -m mcp_servers.playwright_mcp_server
python -m mcp_servers.api_test_mcp_server

# SSE 模式（供 Claude Desktop / Cursor 等外部 Agent 接入）
python -m mcp_servers.playwright_mcp_server --transport sse --port 8001
python -m mcp_servers.api_test_mcp_server --transport sse --port 8002
```

Playwright 工具：`browser_navigate / browser_click / browser_fill / browser_get_text / browser_screenshot / browser_wait_for_selector / browser_close`

API Test 工具：`http_request / assert_status / assert_json_field / assert_json_contains / set_base_url / api_run_case`

## 执行模式说明

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `simulated` | 不调用真实工具，用例标记通过并记录计划 | 演示 / 无浏览器环境 / CI |
| `mcp` | 通过 MCP 协议调用真实浏览器与 HTTP 工具 | 有执行环境（本地/Docker） |

`mcp` 模式下若 MCP Server 不可用，自动降级为 `simulated_fallback` 并记录原因，保证流程不中断。

## 无 API Key 降级

未配置 `OPENAI_API_KEY` 或设置 `ITEST_MOCK_LLM=1` 时，需求分析 Agent 自动切换为**规则解析**（基于 Markdown 标题层级提取功能树、优先级、验收条件），保证整个流程可离线演示。

## 测试

```bash
python -m pytest tests/ -q
```

覆盖：状态模型、工作流拓扑、需求分析、用例生成、评审规则、追溯矩阵、导出器、报告生成、重试机制、端到端流程。

## 面试可讲的技术点

1. Agent Loop：模型决策 → 工具调用 → 结果回填 → 下一步规划
2. Function Calling 与 MCP 的关系：前者是模型调用工具的能力，后者是工具生态接入标准
3. 评审回退如何收敛：覆盖率硬门槛 + 迭代上限 + 确定性补充规则
4. 双向追溯如何实现：需求原文行号引用 + 正向/反向索引
5. 增量更新如何控制变更影响面：按功能 ID 过滤 + 继承历史分析
6. 工具调用安全：参数校验、超时、白名单、模拟降级
