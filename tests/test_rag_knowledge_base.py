"""
iTest-Agent RAG 知识库模块单元测试

测试覆盖：
- TestKnowledgeBase 初始化
- 测试方法论加载与检索
- 历史用例库加载与检索
- 混合检索功能
- 知识库统计与管理
"""

import os

import pytest

# 需要真实 embedding 模型（torch），CI 默认跳过
pytestmark = pytest.mark.heavy


class TestKnowledgeBaseInit:
    """测试知识库初始化"""

    def test_init_creates_collections(self, kb):
        """验证初始化后自动创建两个集合"""
        stats = kb.get_collection_stats()
        assert "methodology_count" in stats
        assert "test_cases_count" in stats
        assert stats["methodology_count"] == 0  # 新实例无数据
        assert stats["test_cases_count"] == 0

    def test_init_with_custom_persist_dir(self):
        """验证自定义持久化目录"""
        import tempfile
        from knowledge_base.rag_knowledge_base import TestKnowledgeBase

        with tempfile.TemporaryDirectory() as tmpdir:
            kb = TestKnowledgeBase(persist_directory=tmpdir)
            assert kb.persist_directory == tmpdir
            stats = kb.get_collection_stats()
            assert stats["methodology_count"] == 0


class TestMethodologyLoading:
    """测试方法论加载与检索"""

    def test_load_methodology_from_md(self, kb):
        """验证从 Markdown 加载测试方法论"""
        md_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "knowledge_base",
            "testing_methodology.md",
        )
        count = kb.load_methodology_from_md(md_path)
        assert count > 0, "应成功加载至少 1 个文档块"
        stats = kb.get_collection_stats()
        assert stats["methodology_count"] == count

    def test_search_methodology_returns_results(self, kb_loaded):
        """验证方法论检索返回相关结果"""
        results = kb_loaded.search_methodology("等价类划分", n_results=3)
        assert len(results) > 0, "应返回至少 1 条结果"
        # 结果应包含 content 和 metadata
        for r in results:
            assert "content" in r
            assert "metadata" in r
            assert len(r["content"]) > 0

    def test_search_methodology_relevance(self, kb_loaded):
        """验证方法论检索的相关性：搜索'登录测试'应返回功能测试相关内容"""
        results = kb_loaded.search_methodology("登录功能测试用例设计", n_results=5)
        assert len(results) > 0
        # 检查首个结果的相关性分数（cosine similarity）
        top_score = results[0].get("score", 0)
        assert top_score > 0, "首个结果应有正相关度分数"


class TestCaseLibraryLoading:
    """测试用例库加载与检索"""

    def test_load_test_cases_from_json(self, kb):
        """验证从 JSON 加载历史用例库"""
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "knowledge_base",
            "test_cases_library.json",
        )
        count = kb.load_test_cases_from_json(json_path)
        assert count == 20, f"应有 20 条用例，实际加载 {count} 条"
        stats = kb.get_collection_stats()
        assert stats["test_cases_count"] == 20

    def test_search_test_cases_by_topic(self, kb_loaded):
        """验证按主题检索用例：搜索'登录'应返回登录相关用例"""
        results = kb_loaded.search_test_cases("用户登录测试", n_results=5)
        assert len(results) > 0
        # 首个结果应包含登录相关内容
        top_content = results[0]["content"].lower()
        assert "登录" in top_content or "login" in top_content

    def test_search_test_cases_metadata(self, kb_loaded):
        """验证用例检索结果的 metadata 完整性"""
        results = kb_loaded.search_test_cases("支付订单", n_results=3)
        assert len(results) > 0
        for r in results:
            meta = r.get("metadata", {})
            assert "title" in meta, "metadata 应包含 title"
            assert "priority" in meta, "metadata 应包含 priority"
            assert "module" in meta, "metadata 应包含 module"


class TestHybridSearch:
    """测试混合检索"""

    def test_hybrid_search_returns_both_sources(self, kb_loaded):
        """验证混合检索同时返回方法论和用例库结果"""
        results = kb_loaded.search_hybrid("如何测试登录功能", n_results=3)
        assert "methodology" in results
        assert "test_cases" in results
        # 两个来源至少一个有结果
        has_methodology = len(results["methodology"]) > 0
        has_cases = len(results["test_cases"]) > 0
        assert has_methodology or has_cases, "至少一个数据源应返回结果"


class TestKnowledgeBaseManagement:
    """测试知识库管理功能"""

    def test_get_collection_stats_after_loading(self, kb_loaded):
        """验证加载后的统计信息正确"""
        stats = kb_loaded.get_collection_stats()
        assert stats["methodology_count"] > 0
        assert stats["test_cases_count"] == 20

    def test_reset_collection(self, kb_loaded):
        """验证重置集合功能"""
        # 获取重置前统计
        stats_before = kb_loaded.get_collection_stats()
        assert stats_before["methodology_count"] > 0

        # 重置方法论集合
        kb_loaded.reset_collection("testing_methodology")

        # 验证已清空
        stats_after = kb_loaded.get_collection_stats()
        assert stats_after["methodology_count"] == 0
        # 用例库不受影响
        assert stats_after["test_cases_count"] > 0
