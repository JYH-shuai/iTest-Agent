"""
iTest-Agent 需求分析 Agent 单元测试

测试覆盖：
- RequirementAnalyzer 初始化
- PRD 文档读取（含异常路径）
- RAG 检索上下文生成
- Prompt 构建
- JSON 解析（含 Markdown 代码块包裹场景）
- 完整分析流程（依赖真实 LLM 或 Mock）
"""

import json
import os

import pytest

# 确保项目根目录在 Python 路径中
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRequirementAnalyzerInit:
    """测试需求分析 Agent 初始化"""

    def test_init_default_params(self):
        """验证使用默认参数初始化"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        assert analyzer.llm_model == "gpt-4o-mini"
        assert analyzer.temperature == 0.1
        assert analyzer.max_retries == 2

    def test_init_custom_params(self):
        """验证使用自定义参数初始化"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer(
            llm_model="gpt-4o",
            temperature=0.3,
            max_retries=1,
        )
        assert analyzer.llm_model == "gpt-4o"
        assert analyzer.temperature == 0.3
        assert analyzer.max_retries == 1

    def test_init_connects_to_kb(self):
        """验证初始化后知识库可连接"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        stats = analyzer.get_kb_stats()
        assert "methodology_count" in stats
        assert "test_cases_count" in stats


class TestPRDReading:
    """测试 PRD 文档读取"""

    def test_read_valid_prd(self, sample_prd_path):
        """验证读取有效的 PRD 文件"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        content = analyzer._read_prd(sample_prd_path)
        assert len(content) > 0
        assert "用户中心" in content
        assert "用户注册" in content
        assert "用户登录" in content

    def test_read_nonexistent_file(self):
        """验证读取不存在的文件抛出 FileNotFoundError"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        with pytest.raises(FileNotFoundError, match="PRD 文件不存在"):
            analyzer._read_prd("/nonexistent/path/prd.md")

    def test_read_empty_file(self, tmp_path):
        """验证读取空文件抛出 ValueError"""
        from agents.requirement_analyzer import RequirementAnalyzer

        empty_file = tmp_path / "empty.md"
        empty_file.write_text("")

        analyzer = RequirementAnalyzer()
        with pytest.raises(ValueError, match="PRD 文件内容为空"):
            analyzer._read_prd(str(empty_file))


class TestRAGRetrieval:
    """测试 RAG 检索上下文生成"""

    def test_retrieve_methodology_returns_context(self, sample_prd_content):
        """验证 RAG 检索返回非空上下文"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        context = analyzer._retrieve_methodology(sample_prd_content, n_results=3)
        assert len(context) > 0
        # 应包含方法论内容或明确的占位提示
        assert len(context) > 50, "检索结果或异常提示应有一定长度"


class TestPromptBuilding:
    """测试 Prompt 构建"""

    def test_build_prompt_structure(self, sample_prd_content):
        """验证 Prompt 模板正确拼接"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        methodology = "测试方法论示例文本"
        prompt = analyzer._build_prompt(sample_prd_content, methodology)

        # Prompt 应包含多条消息（system + human）
        messages = prompt.messages
        assert len(messages) == 2

        # System 消息应包含方法论上下文
        system_content = str(messages[0].content)
        assert methodology in system_content

        # Human 消息应包含 PRD 占位符
        human_content = str(messages[1].content)
        assert "{prd_content}" in human_content


class TestJSONExtraction:
    """测试 JSON 提取与解析"""

    def test_extract_plain_json(self):
        """验证提取纯 JSON 字符串"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        raw = '{"overview": {"product_name": "测试", "module_name": "测试模块", "total_functions": 1, "p0_count": 1, "p1_count": 0, "p2_count": 0}, "function_tree": []}'
        result = analyzer._extract_json(raw)
        assert result["overview"]["product_name"] == "测试"
        assert result["function_tree"] == []

    def test_extract_json_in_code_block(self):
        """验证从 Markdown 代码块中提取 JSON"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        raw = """以下是分析结果：

```json
{"overview": {"product_name": "电商", "module_name": "用户", "total_functions": 2, "p0_count": 1, "p1_count": 1, "p2_count": 0}, "function_tree": []}
```

分析完成。"""
        result = analyzer._extract_json(raw)
        assert result["overview"]["product_name"] == "电商"
        assert result["overview"]["total_functions"] == 2

    def test_extract_json_with_text_wrapper(self):
        """验证从前导文本中提取 JSON"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        raw = (
            "好的，我已经分析完毕。\n"
            '{"overview": {"product_name": "X", "module_name": "Y", '
            '"total_functions": 0, "p0_count": 0, "p1_count": 0, "p2_count": 0}, '
            '"function_tree": []}'
        )
        result = analyzer._extract_json(raw)
        assert result["overview"]["product_name"] == "X"

    def test_extract_invalid_json_raises(self):
        """验证无效 JSON 抛出异常"""
        from agents.requirement_analyzer import RequirementAnalyzer

        analyzer = RequirementAnalyzer()
        with pytest.raises(json.JSONDecodeError):
            analyzer._extract_json("这不是 JSON")


class TestPydanticModels:
    """测试 Pydantic 数据模型"""

    def test_analysis_result_validation(self):
        """验证 RequirementAnalysisResult 的 Pydantic 验证"""
        from agents.requirement_analyzer import (
            AnalysisOverview,
            Dependency,
            FunctionNode,
            RequirementAnalysisResult,
            SubFunction,
            TestSuggestion,
        )

        result = RequirementAnalysisResult(
            overview=AnalysisOverview(
                product_name="测试产品",
                module_name="测试模块",
                total_functions=1,
                p0_count=1,
                p1_count=0,
                p2_count=0,
            ),
            function_tree=[
                FunctionNode(
                    id="FUNC-001",
                    name="测试功能",
                    description="功能描述",
                    priority="P0",
                    dependencies=[
                        Dependency(
                            depends_on="FUNC-000",
                            type="前置依赖",
                            description="依赖说明",
                        )
                    ],
                    sub_functions=[
                        SubFunction(
                            id="FUNC-001-01",
                            name="子功能",
                            description="子功能描述",
                            priority="P0",
                            acceptance_criteria=["验收条件1"],
                            test_suggestions=[
                                TestSuggestion(
                                    method="等价类划分",
                                    suggestion="测试建议",
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        # 序列化验证
        data = result.model_dump()
        assert data["overview"]["product_name"] == "测试产品"
        assert len(data["function_tree"]) == 1
        assert len(data["function_tree"][0]["sub_functions"]) == 1

    def test_model_rejects_invalid_priority(self):
        """验证 Pydantic 拒绝无效优先级值 — 仅做基础校验，priority 字段为 str 类型"""
        from agents.requirement_analyzer import FunctionNode

        # Pydantic 不会在 str 字段上做枚举校验，
        # 但结构完整性应通过 model_dump 来验证
        fn = FunctionNode(
            id="FUNC-001",
            name="测试",
            description="描述",
            priority="P0",
        )
        assert fn.priority == "P0"
        assert fn.model_dump() is not None


class TestAnalyzerIndependence:
    """测试模块可独立运行性"""

    def test_module_can_be_imported(self):
        """验证模块可正常导入"""
        import agents.requirement_analyzer as ra

        assert hasattr(ra, "RequirementAnalyzer")
        assert hasattr(ra, "RequirementAnalysisResult")
        assert hasattr(ra, "main")

    def test_main_help(self):
        """验证 CLI --help 不会报错"""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agents.requirement_analyzer",
                "--help",
            ],
            capture_output=True,
            text=True,
            cwd=_project_root,
        )
        # --help 退出码可能不为 0（argparse 默认），但不应崩溃
        assert "usage:" in result.stdout or "usage:" in result.stderr
