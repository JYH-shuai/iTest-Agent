"""
iTest-Agent RAG 知识库模块
基于 Chroma + Sentence-Transformers 的测试方法论与历史用例检索系统
"""

import json
import os
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions


@dataclass
class KnowledgeDocument:
    """知识库文档结构"""
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    doc_id: Optional[str] = None


class TestKnowledgeBase:
    """
    测试知识库：管理测试方法论和历史用例的向量化检索

    功能：
    - 加载测试方法论文档（Markdown）并向量化
    - 加载历史用例库（JSON）并向量化
    - 基于语义相似度检索相关知识
    - 支持增量更新
    """

    COLLECTION_METHODOLOGY = "testing_methodology"
    COLLECTION_TEST_CASES = "test_cases_library"

    def __init__(
        self,
        persist_directory: str = "chroma_db",
        embedding_model: str = "all-MiniLM-L6-v2",
        collection_metadata: Optional[Dict] = None,
    ):
        self.persist_directory = persist_directory
        self.embedding_model = embedding_model
        self.collection_metadata = collection_metadata or {"hnsw:space": "cosine"}

        # 初始化 Chroma 客户端（持久化模式）
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False),
        )

        # 使用 Sentence-Transformers 嵌入函数
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model,
        )

        # 初始化或获取集合
        self.methodology_collection = self._get_or_create_collection(
            self.COLLECTION_METHODOLOGY
        )
        self.test_cases_collection = self._get_or_create_collection(
            self.COLLECTION_TEST_CASES
        )

    def _get_or_create_collection(self, name: str):
        """获取或创建集合"""
        try:
            return self.client.get_collection(
                name=name,
                embedding_function=self.embedding_fn,
            )
        except Exception:
            return self.client.create_collection(
                name=name,
                embedding_function=self.embedding_fn,
                metadata=self.collection_metadata,
            )

    # ========== 文档加载 ==========

    def load_methodology_from_md(self, file_path: str, chunk_size: int = 500) -> int:
        """
        从 Markdown 文件加载测试方法论，按段落分块后向量化

        Args:
            file_path: Markdown 文件路径
            chunk_size: 分块大小（字符数）

        Returns:
            加载的文档块数量
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 按二级标题分块
        chunks = self._split_by_headings(content, level=2)

        # 若某块超过 chunk_size，进一步按段落拆分
        final_chunks = []
        for chunk in chunks:
            if len(chunk) > chunk_size:
                final_chunks.extend(
                    self._split_by_paragraphs(chunk, chunk_size)
                )
            else:
                final_chunks.append(chunk)

        docs = []
        ids = []
        metadatas = []

        for i, chunk in enumerate(final_chunks):
            doc_id = f"methodology_{i:04d}"
            docs.append(chunk)
            ids.append(doc_id)
            metadatas.append({
                "source": os.path.basename(file_path),
                "chunk_index": i,
                "category": "testing_methodology",
            })

        self.methodology_collection.add(
            documents=docs,
            ids=ids,
            metadatas=metadatas,
        )
        return len(docs)

    def load_test_cases_from_json(self, file_path: str) -> int:
        """
        从 JSON 文件加载历史用例库，每个用例作为一个文档

        Args:
            file_path: JSON 文件路径

        Returns:
            加载的用例数量
        """
        with open(file_path, "r", encoding="utf-8") as f:
            cases = json.load(f)

        docs = []
        ids = []
        metadatas = []

        for case in cases:
            case_id = case.get("id", f"case_{len(docs):04d}")
            # 将用例结构化为可检索文本
            doc_text = self._format_test_case(case)
            docs.append(doc_text)
            ids.append(case_id)
            metadatas.append({
                "title": case.get("title", ""),
                "module": case.get("module", ""),
                "type": case.get("type", ""),
                "priority": case.get("priority", "P2"),
                "category": "test_case",
            })

        self.test_cases_collection.add(
            documents=docs,
            ids=ids,
            metadatas=metadatas,
        )
        return len(docs)

    # ========== 检索接口 ==========

    def search_methodology(
        self, query: str, n_results: int = 5
    ) -> List[Dict[str, Any]]:
        """在测试方法论中检索"""
        results = self.methodology_collection.query(
            query_texts=[query],
            n_results=n_results,
        )
        return self._format_results(results)

    def search_test_cases(
        self, query: str, n_results: int = 5
    ) -> List[Dict[str, Any]]:
        """在历史用例库中检索"""
        results = self.test_cases_collection.query(
            query_texts=[query],
            n_results=n_results,
        )
        return self._format_results(results)

    def search_hybrid(
        self, query: str, n_results: int = 5
    ) -> Dict[str, List[Dict[str, Any]]]:
        """混合检索：同时检索方法论和用例库"""
        return {
            "methodology": self.search_methodology(query, n_results),
            "test_cases": self.search_test_cases(query, n_results),
        }

    # ========== 管理接口 ==========

    def get_collection_stats(self) -> Dict[str, int]:
        """获取知识库统计信息"""
        return {
            "methodology_count": self.methodology_collection.count(),
            "test_cases_count": self.test_cases_collection.count(),
        }

    def reset_collection(self, collection_name: str):
        """重置指定集合"""
        try:
            self.client.delete_collection(collection_name)
        except Exception:
            pass
        # 重新创建
        if collection_name == self.COLLECTION_METHODOLOGY:
            self.methodology_collection = self._get_or_create_collection(
                self.COLLECTION_METHODOLOGY
            )
        elif collection_name == self.COLLECTION_TEST_CASES:
            self.test_cases_collection = self._get_or_create_collection(
                self.COLLECTION_TEST_CASES
            )

    def delete_document(self, collection_name: str, doc_id: str):
        """删除指定文档"""
        collection = self._get_or_create_collection(collection_name)
        collection.delete(ids=[doc_id])

    # ========== 工具方法 ==========

    @staticmethod
    def _split_by_headings(text: str, level: int = 2) -> List[str]:
        """按 Markdown 标题分块"""
        prefix = "#" * level + " "
        sections = text.split("\n" + prefix)
        result = []
        for i, section in enumerate(sections):
            if i == 0 and not section.startswith(prefix.lstrip()):
                # 第一个块可能没有标题
                if section.strip():
                    result.append(section.strip())
            else:
                result.append((prefix + section).strip())
        return result

    @staticmethod
    def _split_by_paragraphs(text: str, max_len: int) -> List[str]:
        """按段落拆分长文本"""
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) > max_len and current:
                chunks.append(current.strip())
                current = p
            else:
                current += "\n\n" + p if current else p
        if current.strip():
            chunks.append(current.strip())
        return chunks

    @staticmethod
    def _format_test_case(case: Dict) -> str:
        """将用例字典格式化为可检索文本"""
        parts = []
        if case.get("title"):
            parts.append(f"用例标题: {case['title']}")
        if case.get("module"):
            parts.append(f"所属模块: {case['module']}")
        if case.get("type"):
            parts.append(f"用例类型: {case['type']}")
        if case.get("precondition"):
            parts.append(f"前置条件: {case['precondition']}")
        if case.get("steps"):
            steps_str = "; ".join(
                f"步骤{s.get('step', '')}: {s.get('action', '')} -> 预期: {s.get('expected', '')}"
                for s in case["steps"]
            )
            parts.append(f"测试步骤: {steps_str}")
        if case.get("tags"):
            parts.append(f"标签: {', '.join(case['tags'])}")
        return "\n".join(parts)

    @staticmethod
    def _format_results(results: Dict) -> List[Dict[str, Any]]:
        """格式化 Chroma 查询结果"""
        formatted = []
        if not results.get("documents") or not results["documents"][0]:
            return formatted

        for i, doc in enumerate(results["documents"][0]):
            item = {"content": doc}
            if results.get("metadatas") and results["metadatas"][0]:
                item["metadata"] = results["metadatas"][0][i]
            if results.get("distances") and results["distances"][0]:
                item["score"] = 1 - results["distances"][0][i]  # cosine distance to similarity
            formatted.append(item)
        return formatted


class KnowledgeBaseLoader:
    """
    知识库加载器：一次性加载所有知识文档到知识库
    """

    def __init__(self, kb: TestKnowledgeBase):
        self.kb = kb

    def load_all(self, data_dir: str) -> Dict[str, Any]:
        """
        加载 data_dir 下所有知识文档

        目录结构:
            data_dir/
                testing_methodology.md   -- 测试方法论文档
                test_cases_library.json  -- 历史用例库

        Returns:
            加载统计
        """
        stats = {}

        methodology_path = os.path.join(data_dir, "testing_methodology.md")
        if os.path.exists(methodology_path):
            count = self.kb.load_methodology_from_md(methodology_path)
            stats["methodology_chunks"] = count

        cases_path = os.path.join(data_dir, "test_cases_library.json")
        if os.path.exists(cases_path):
            count = self.kb.load_test_cases_from_json(cases_path)
            stats["test_cases_count"] = count

        return stats
