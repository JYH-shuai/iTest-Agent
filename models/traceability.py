"""
iTest-Agent 追溯矩阵模块

定义双向追溯链接（RequirementRef / TraceLink）和追溯矩阵（TraceabilityMatrix），
支持：
- 正向追溯：用例 → 需求原文（行号/段落）
- 反向追溯：功能点 → 关联用例
- 按关键词检索关联用例
- 覆盖度统计报告
- JSON / Markdown 可视化导出
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# 数据模型
# =============================================================================


class RequirementRef(BaseModel):
    """需求原文引用 — 精确标注需求文档中的位置与内容"""

    section_title: str = Field(
        default="", description="章节标题（从 Markdown # / ## 提取）"
    )
    paragraph_index: int = Field(
        default=0, ge=0, description="段落序号（0-based，在所属章节内）"
    )
    line_start: int = Field(
        default=1, ge=1, description="起始行号（1-based）"
    )
    line_end: int = Field(
        default=1, ge=1, description="结束行号（1-based，含）"
    )
    text: str = Field(
        default="", max_length=300, description="引用原文，最多 300 字符"
    )
    keywords: List[str] = Field(
        default_factory=list, description="从引用文本中提取的关键词"
    )


class TraceLink(BaseModel):
    """追溯链接 — 连接需求原文与测试用例"""

    link_id: str = Field(
        ..., min_length=1, description="唯一标识，如 LINK-001"
    )
    requirement_ref: RequirementRef = Field(
        default_factory=RequirementRef, description="需求原文引用"
    )
    test_case_ids: List[str] = Field(
        default_factory=list, description="关联的用例 ID 列表"
    )
    function_id: str = Field(
        default="", description="关联的功能 ID（SubFunction.id）"
    )
    direction: str = Field(
        default="forward",
        description="追溯方向：forward=用例→需求，backward=需求→用例",
    )


# =============================================================================
# 追溯矩阵
# =============================================================================


class TraceabilityMatrix:
    """
    追溯矩阵 — 双向索引核心类

    管理正向（用例→需求原文）与反向（功能→用例）双重映射，
    并提供 JSON 序列化 / Markdown 可视化 / 覆盖度统计 等查询能力。

    Attributes:
        links: 以 link_id 为键的所有 TraceLink
        forward_index: 用例 ID → 需求引用 link_id 列表
        backward_index: 功能 ID → 用例 ID 列表
    """

    def __init__(self) -> None:
        """初始化空的追溯矩阵"""
        self.links: Dict[str, TraceLink] = {}
        self.forward_index: Dict[str, List[str]] = {}
        self.backward_index: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------

    def _extract_section_title(self, line: str) -> str:
        """从 Markdown 标题行提取章节标题"""
        match = re.match(r"^#{1,6}\s+(.+)", line)
        return match.group(1).strip() if match else ""

    def _extract_keywords(self, text: str, max_kw: int = 5) -> List[str]:
        """
        从文本中提取关键词

        简单实现：按空白分词后过滤长度 >= 2 的中文/英文词，取前 max_kw 个。
        """
        tokens: List[str] = re.findall(r"[\w\u4e00-\u9fff]{2,}", text)
        seen: set = set()
        result: List[str] = []
        for t in tokens:
            if t.lower() not in seen:
                seen.add(t.lower())
                result.append(t)
                if len(result) >= max_kw:
                    break
        return result

    def build_from_prd(
        self,
        prd_path: str,
        function_tree: List[Dict[str, Any]],
        test_suite: Any,
    ) -> None:
        """
        从 PRD 文档、功能树、测试用例集构建追溯矩阵

        流程：
        1. 逐行读取 PRD 全文，记录当前章节标题
        2. 对每个功能点，用名称/关键词在 PRD 中匹配相关段落
        3. 创建 TraceLink 并填充正向/反向索引

        Args:
            prd_path: PRD 文档路径
            function_tree: 功能树列表（FunctionNode dict 列表）
            test_suite: TestSuite 实例（含 test_cases）
        """
        # 重置索引
        self.links.clear()
        self.forward_index.clear()
        self.backward_index.clear()

        # ---- 读取 PRD 并记录章节/行信息 ----
        prd_lines: List[str] = []
        current_section: str = ""
        try:
            if os.path.exists(prd_path):
                with open(prd_path, "r", encoding="utf-8") as f:
                    prd_lines = f.readlines()
        except Exception:
            prd_lines = []

        # 预计算每行所属章节
        section_for_line: Dict[int, str] = {}
        for i, line in enumerate(prd_lines):
            title = self._extract_section_title(line)
            if title:
                current_section = title
            section_for_line[i] = current_section

        # ---- 收集所有 SubFunction ----
        all_sub_functions: List[Dict[str, Any]] = []
        for func_node in function_tree:
            sub_funcs = func_node.get("sub_functions", [])
            all_sub_functions.extend(sub_funcs)

        # 收集所有用例（按 function_id 分组）
        case_ids_by_function: Dict[str, List[str]] = {}
        if test_suite is not None:
            for tc in getattr(test_suite, "test_cases", []):
                fid = getattr(tc, "function_id", "")
                cid = getattr(tc, "case_id", "")
                if fid:
                    case_ids_by_function.setdefault(fid, []).append(cid)

        # ---- 为每个 SubFunction 创建 TraceLink ----
        link_counter = 0
        for sf in all_sub_functions:
            sf_id = sf.get("id", "")
            sf_name = sf.get("name", "")
            sf_desc = sf.get("description", "")

            # 用名称/描述在 PRD 中搜索匹配行
            search_terms = [sf_name]
            if sf_desc:
                # 取描述的前几个词作为搜索关键词
                search_terms.append(sf_desc[:30])

            matched_lines: List[int] = []
            for term in search_terms:
                if not term:
                    continue
                for i, line in enumerate(prd_lines):
                    if term in line and i not in matched_lines:
                        matched_lines.append(i)

            # 构建 RequirementRef
            if matched_lines:
                # 取第一个匹配行所在段落（向上下扩展到空行）
                first_match = matched_lines[0]
                line_start = first_match
                line_end = first_match + 1

                # 向上找到段落起始（空行或标题行）
                while line_start > 0:
                    prev = prd_lines[line_start - 1].strip()
                    if prev == "" or prev.startswith("#"):
                        break
                    line_start -= 1

                # 向下找到段落结束
                while line_end < len(prd_lines):
                    curr = prd_lines[line_end].strip()
                    if curr == "" or curr.startswith("#"):
                        break
                    line_end += 1

                paragraph_text = "".join(prd_lines[line_start:line_end]).strip()
                section_title = section_for_line.get(first_match, "")
                keywords = self._extract_keywords(sf_name + " " + sf_desc)
            else:
                line_start = 1
                line_end = 1
                paragraph_text = sf_name
                section_title = ""
                keywords = [sf_name]

            ref = RequirementRef(
                section_title=section_title,
                paragraph_index=0,
                line_start=line_start + 1,   # 转为 1-based
                line_end=line_end,
                text=paragraph_text[:300],
                keywords=keywords,
            )

            link_counter += 1
            link_id = f"LINK-{link_counter:03d}"
            case_ids = case_ids_by_function.get(sf_id, [])

            link = TraceLink(
                link_id=link_id,
                requirement_ref=ref,
                test_case_ids=case_ids,
                function_id=sf_id,
                direction="forward",
            )

            self.links[link_id] = link

            # 正向索引：用例 ID → link_id
            for cid in case_ids:
                self.forward_index.setdefault(cid, []).append(link_id)

            # 反向索引：功能 ID → 用例 ID
            if case_ids:
                self.backward_index[sf_id] = case_ids

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_requirements_for_case(self, case_id: str) -> List[RequirementRef]:
        """
        正向追溯：给定用例 ID，返回关联的需求原文引用列表

        Args:
            case_id: 用例 ID

        Returns:
            关联的 RequirementRef 列表
        """
        link_ids = self.forward_index.get(case_id, [])
        refs: List[RequirementRef] = []
        for lid in link_ids:
            link = self.links.get(lid)
            if link:
                refs.append(link.requirement_ref)
        return refs

    def get_cases_for_function(self, function_id: str) -> List[str]:
        """
        反向追溯：给定功能 ID，返回关联的用例 ID 列表

        Args:
            function_id: 功能 ID（SubFunction.id）

        Returns:
            用例 ID 列表
        """
        return self.backward_index.get(function_id, [])

    def get_cases_for_keyword(self, keyword: str) -> List[str]:
        """
        按关键词检索关联的用例 ID

        匹配 RequirementRef.keywords 中是否包含该关键词。

        Args:
            keyword: 检索关键词

        Returns:
            用例 ID 列表（去重）
        """
        case_ids: set = set()
        kw_lower = keyword.lower()
        for link in self.links.values():
            for kw in link.requirement_ref.keywords:
                if kw_lower in kw.lower():
                    case_ids.update(link.test_case_ids)
                    break
        return sorted(case_ids)

    def coverage_report(self) -> Dict[str, Any]:
        """
        统计追溯覆盖度

        Returns:
            字典包含：
            - total_functions: 已有 TraceLink 的功能点数
            - linked_functions: 已关联用例的功能点数
            - unlinked_functions: 未关联用例的功能 ID 列表
            - coverage_rate: 覆盖率（0.0 ~ 1.0）
        """
        total = len(self.links)
        linked_ids: List[str] = []
        unlinked_ids: List[str] = []
        for lid, link in self.links.items():
            if link.test_case_ids:
                linked_ids.append(link.function_id)
            else:
                unlinked_ids.append(link.function_id)

        coverage = len(linked_ids) / total if total > 0 else 0.0

        return {
            "total_functions": total,
            "linked_functions": len(linked_ids),
            "unlinked_functions": unlinked_ids,
            "coverage_rate": coverage,
        }

    # ------------------------------------------------------------------
    # 序列化与导出
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        序列化为字典（用于 JSON 导出）

        Returns:
            包含 links / forward_index / backward_index 的字典
        """
        return {
            "links": {lid: link.model_dump() for lid, link in self.links.items()},
            "forward_index": dict(self.forward_index),
            "backward_index": dict(self.backward_index),
        }

    def to_markdown(self) -> str:
        """
        生成追溯矩阵的 Markdown 可视化表格

        Returns:
            Markdown 格式字符串
        """
        lines: List[str] = [
            "# 追溯矩阵 (Traceability Matrix)",
            "",
            "| Link ID | 功能 ID | 章节标题 | 需求原文（前 80 字符） | 行号 | 关联用例数 |",
            "|---------|---------|----------|------------------------|------|-----------|",
        ]

        for lid, link in self.links.items():
            ref = link.requirement_ref
            text_snippet = ref.text[:80].replace("|", "\\|").replace("\n", " ")
            case_count = len(link.test_case_ids)
            area = f"L{ref.line_start}-L{ref.line_end}"
            lines.append(
                f"| {lid} | {link.function_id} | {ref.section_title[:20]} "
                f"| {text_snippet} | {area} | {case_count} |"
            )

        # 追加覆盖度报告
        report = self.coverage_report()
        lines.extend([
            "",
            "## 覆盖度报告",
            "",
            f"- 总功能点数: {report['total_functions']}",
            f"- 已关联用例: {report['linked_functions']}",
            f"- 未关联用例: {len(report['unlinked_functions'])}",
            f"- 覆盖率: {report['coverage_rate']:.1%}",
        ])

        if report["unlinked_functions"]:
            lines.append("")
            lines.append("### 未关联用例的功能点")
            for fid in report["unlinked_functions"]:
                lines.append(f"- `{fid}`")

        return "\n".join(lines)
