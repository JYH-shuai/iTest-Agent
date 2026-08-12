"""
知识库加载与验证脚本
加载测试方法论和用例库到 Chroma 向量数据库，并运行检索验证
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge_base.rag_knowledge_base import TestKnowledgeBase, KnowledgeBaseLoader


def main():
    # 数据目录
    data_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
    persist_dir = os.path.join(os.path.dirname(__file__), "chroma_db")

    print("=" * 60)
    print("iTest-Agent 知识库加载与验证")
    print("=" * 60)

    # 初始化知识库
    kb = TestKnowledgeBase(persist_directory=persist_dir)
    loader = KnowledgeBaseLoader(kb)

    # 加载所有知识文档
    print("\n[1/3] 加载知识文档...")
    stats = loader.load_all(data_dir)
    print(f"  - 测试方法论: {stats.get('methodology_chunks', 0)} 个文档块")
    print(f"  - 历史用例: {stats.get('test_cases_count', 0)} 条用例")

    # 获取统计
    print("\n[2/3] 知识库统计...")
    kb_stats = kb.get_collection_stats()
    print(f"  - 方法论集合: {kb_stats['methodology_count']} 条")
    print(f"  - 用例库集合: {kb_stats['test_cases_count']} 条")

    # 检索验证
    print("\n[3/3] 检索验证...")

    test_queries = [
        ("如何设计登录功能的测试用例？", "用例设计"),
        ("等价类划分和边界值分析有什么区别？", "测试方法论"),
        ("API接口测试需要注意哪些方面？", "接口测试"),
        ("如何测试文件上传功能？", "文件上传"),
        ("性能测试的关键指标是什么？", "性能测试"),
    ]

    for query, category in test_queries:
        print(f"\n  >>> 查询: '{query}' ({category})")
        results = kb.search_hybrid(query, n_results=3)

        if results["methodology"]:
            print(f"  [方法论] 命中 {len(results['methodology'])} 条:")
            for i, r in enumerate(results["methodology"]):
                score = r.get("score", 0)
                content_preview = r["content"][:80].replace("\n", " ")
                print(f"    {i+1}. [score={score:.3f}] {content_preview}...")

        if results["test_cases"]:
            print(f"  [用例库] 命中 {len(results['test_cases'])} 条:")
            for i, r in enumerate(results["test_cases"]):
                meta = r.get("metadata", {})
                score = r.get("score", 0)
                print(f"    {i+1}. [{meta.get('title', 'N/A')}] [priority={meta.get('priority', '?')}] [score={score:.3f}]")

    print("\n" + "=" * 60)
    print("知识库加载完成！")
    print(f"向量数据存储位置: {os.path.abspath(persist_dir)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
