"""
pytest 配置与 fixture — 为 iTest-Agent 测试提供共享资源

Fixtures:
- test_project_root: 项目根目录路径
- kb: 初始化 TestKnowledgeBase 实例（使用临时目录，避免污染真实数据）
- sample_prd_path: 获取样例 PRD 文件路径
- analyzer: 初始化 RequirementAnalyzer 实例（使用 mock LLM 避免真实 API 调用）
"""

import os
import sys
import tempfile

import pytest

# 将项目根目录加入 Python 路径
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@pytest.fixture(scope="session")
def test_project_root() -> str:
    """返回项目根目录的绝对路径"""
    return _project_root


@pytest.fixture
def sample_prd_path(test_project_root: str) -> str:
    """返回样例 PRD 文件路径"""
    path = os.path.join(test_project_root, "tests", "sample_prd.md")
    if not os.path.exists(path):
        raise FileNotFoundError(f"样例 PRD 文件不存在: {path}")
    return path


@pytest.fixture
def kb():
    """
    初始化 TestKnowledgeBase 实例（使用临时目录）

    每次测试独立创建，避免测试间数据污染。
    测试结束后自动清理临时目录。
    """
    # 使用 NamedTemporaryFile 获取唯一路径再转为目录
    with tempfile.TemporaryDirectory(prefix="itest_kb_test_") as tmpdir:
        from knowledge_base.rag_knowledge_base import TestKnowledgeBase

        kb_instance = TestKnowledgeBase(persist_directory=tmpdir)
        yield kb_instance
        # TemporaryDirectory 上下文退出时自动清理


@pytest.fixture
def kb_loaded(kb):
    """
    预加载了知识库数据的 TestKnowledgeBase 实例

    加载 testing_methodology.md 和 test_cases_library.json。
    """
    from knowledge_base.rag_knowledge_base import KnowledgeBaseLoader

    loader = KnowledgeBaseLoader(kb)
    data_dir = os.path.join(_project_root, "knowledge_base")
    loader.load_all(data_dir)
    return kb


@pytest.fixture
def sample_prd_content(sample_prd_path: str) -> str:
    """读取样例 PRD 文本内容"""
    with open(sample_prd_path, "r", encoding="utf-8") as f:
        return f.read()


def pytest_configure(config):
    """注册自定义 marker：
    - heavy: 需要真实模型/浏览器的重型测试，默认排除（CI 跳过）
    """
    config.addinivalue_line(
        "markers", "heavy: 需要真实 embedding 模型或浏览器的重型测试，CI 默认跳过"
    )
