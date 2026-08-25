"""
iTest-Agent 需求分析 Agent — 基于 LangChain + LLM 的需求文档分析模块

输入：PRD / Markdown 需求文档路径
输出：结构化关键功能点列表（JSON），包含功能名称、描述、优先级、依赖关系

特性：
- 基于 LangChain ChatOpenAI 调用 LLM 进行需求理解
- 利用 RAG 知识库检索测试方法论辅助分析
- 支持多层级功能分解（一级功能 → 二级子功能）
- 完善的类型注解和 Pydantic 数据模型
- LLM 调用失败自动重试（最多 2 次）
- 可独立运行：python -m agents.requirement_analyzer
"""

import json
import os
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# 添加项目根目录到 Python 路径（支持独立运行）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.prompt_templates import REQUIREMENT_ANALYSIS_PROMPT
from knowledge_base.rag_knowledge_base import TestKnowledgeBase


# =============================================================================
# 规则解析降级（无 API Key / ITEST_MOCK_LLM=1 时使用）
# =============================================================================

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")

_P0_KEYWORDS = ("注册", "登录", "核心", "主流程", "支付", "下单", "创建", "提交")
_P1_KEYWORDS = ("异常", "边界", "安全", "权限", "认证", "校验", "超时")
_API_KEYWORDS = ("接口", "api", "http", "rest", "调用")
_PERF_KEYWORDS = ("性能", "并发", "压力", "负载", "吞吐")


def rule_based_analyze(prd_content: str) -> "RequirementAnalysisResult":
    """
    基于 Markdown 结构的确定性需求分析（LLM 不可用时的降级路径）。

    解析规则：
    - `#` → 产品名称
    - `##` → 模块名称（取第一个非"概述"章节）
    - `###` → 一级功能
    - `####` → 二级子功能
    - 子功能下的 `-` 列表项 → 验收条件
    - 优先级启发式：核心主流程 P0；异常/安全/校验类 P1；其余 P1
    """
    from agents.requirement_analyzer import (
        AnalysisOverview,
        FunctionNode,
        RequirementAnalysisResult,
        SubFunction,
        TestSuggestion,
    )

    product_name = ""
    module_name = ""
    functions: List[FunctionNode] = []
    current_func: Optional[FunctionNode] = None
    current_sub: Optional[SubFunction] = None
    current_acceptance: List[str] = []
    current_desc: List[str] = []
    in_overview = False
    overview_consumed = False

    def _flush_sub() -> None:
        nonlocal current_sub, current_acceptance, current_desc
        if current_sub is not None:
            current_sub.acceptance_criteria = list(current_acceptance)
            current_sub.description = (
                current_sub.description or " ".join(current_desc).strip()
            )
            if current_func is not None:
                current_func.sub_functions.append(current_sub)
        current_sub = None
        current_acceptance = []
        current_desc = []

    def _flush_func() -> None:
        nonlocal current_func
        _flush_sub()
        if current_func is not None:
            functions.append(current_func)
        current_func = None

    lines = prd_content.split("\n")
    for raw in lines:
        line = raw.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip().strip("#").strip()
            if not title:
                continue
            if level == 1:
                product_name = _clean_title(title)
            elif level == 2:
                _flush_func()
                in_overview = any(kw in title for kw in ("概述", "简介", "背景"))
                if not module_name and not in_overview:
                    module_name = title
            elif level == 3:
                _flush_func()
                current_func = FunctionNode(
                    id=f"FUNC-{len(functions) + 1:03d}",
                    name=title,
                    description="",
                    priority=_infer_priority(title),
                    dependencies=[],
                    sub_functions=[],
                )
            elif level == 4:
                _flush_sub()
                if current_func is None:
                    # 容错：无父功能时自动创建
                    current_func = FunctionNode(
                        id=f"FUNC-{len(functions) + 1:03d}",
                        name=title,
                        description="",
                        priority=_infer_priority(title),
                        dependencies=[],
                        sub_functions=[],
                    )
                current_sub = SubFunction(
                    id=f"{current_func.id}-{len(current_func.sub_functions) + 1:02d}",
                    name=title,
                    description="",
                    priority=_infer_priority(title),
                    acceptance_criteria=[],
                    test_suggestions=_infer_suggestions(title),
                    requirement_refs=[],
                )
            continue

        bm = _BULLET_RE.match(line)
        if bm and current_sub is not None:
            current_acceptance.append(bm.group(1).strip())
            continue

        # 非空文本段落 → 作为当前节点描述
        if line.strip() and current_sub is not None:
            current_desc.append(line.strip())
        elif line.strip() and current_func is not None and not current_func.description:
            current_func.description = line.strip()
        elif (
            line.strip()
            and in_overview
            and not overview_consumed
            and not module_name
            and not _HEADING_RE.match(line)
        ):
            # 从模块概述段落提取模块名，如"用户中心模块是……" → "用户中心模块"
            m = re.match(
                r"^([\u4e00-\u9fa5A-Za-z0-9]+?)(模块|系统|平台)",
                line.strip(),
            )
            if m:
                module_name = m.group(1) + m.group(2)
                overview_consumed = True

    _flush_func()

    total = sum(len(f.sub_functions) for f in functions)
    p0 = sum(
        1
        for f in functions
        for sf in f.sub_functions
        if sf.priority == "P0"
    )
    p1 = sum(
        1
        for f in functions
        for sf in f.sub_functions
        if sf.priority == "P1"
    )
    p2 = sum(
        1
        for f in functions
        for sf in f.sub_functions
        if sf.priority == "P2"
    )

    return RequirementAnalysisResult(
        overview=AnalysisOverview(
            product_name=product_name or "未命名产品",
            module_name=module_name or "未命名模块",
            total_functions=total,
            p0_count=p0,
            p1_count=p1,
            p2_count=p2,
        ),
        function_tree=functions,
    )


def _clean_title(title: str) -> str:
    """清理标题：去掉『— 产品需求文档（PRD）』等后缀与编号前缀"""
    for sep in ("—", "-", "（", "(", "｜", "|"):
        if sep in title:
            title = title.split(sep)[0].strip()
    return title.strip()


def _infer_priority(title: str) -> str:
    """优先级启发式：核心主流程 P0；异常/安全/校验类 P1；其余 P1"""
    lower = title.lower()
    if any(kw in lower for kw in _P0_KEYWORDS):
        return "P0"
    if any(kw in lower for kw in _P1_KEYWORDS):
        return "P1"
    return "P1"


def _infer_suggestions(title: str) -> "List[TestSuggestion]":
    """根据标题推断测试建议（规则降级用）"""
    from agents.requirement_analyzer import TestSuggestion

    lower = title.lower()
    suggestions: "List[TestSuggestion]" = []
    if any(kw in lower for kw in ("输入", "填写", "注册", "登录")):
        suggestions.append(
            TestSuggestion(method="等价类划分", suggestion="覆盖合法/非法/空值输入")
        )
        suggestions.append(
            TestSuggestion(method="边界值分析", suggestion="覆盖最小/最大/临界值")
        )
    if any(kw in lower for kw in _API_KEYWORDS):
        suggestions.append(
            TestSuggestion(method="接口测试", suggestion="校验状态码、响应结构与错误码")
        )
    if any(kw in lower for kw in _PERF_KEYWORDS):
        suggestions.append(
            TestSuggestion(method="性能测试", suggestion="验证并发场景下的响应时间与吞吐")
        )
    if any(kw in lower for kw in ("异常", "超时", "失败")):
        suggestions.append(
            TestSuggestion(method="异常场景分析", suggestion="覆盖超时、重试、幂等与降级")
        )
    if not suggestions:
        suggestions.append(
            TestSuggestion(method="场景法", suggestion="覆盖主流程与分支场景")
        )
    return suggestions


# =============================================================================
# Pydantic 数据模型
# =============================================================================


class Dependency(BaseModel):
    """功能依赖关系"""
    depends_on: str = Field(..., description="被依赖的功能 ID")
    type: str = Field(..., description="依赖类型：前置依赖|数据依赖|时序依赖")
    description: str = Field(..., description="依赖说明")


class TestSuggestion(BaseModel):
    """测试建议"""
    method: str = Field(..., description="测试方法名称，如等价类划分、边界值分析")
    suggestion: str = Field(..., description="具体测试建议")


class SubFunction(BaseModel):
    """二级子功能"""
    id: str = Field(..., description="子功能 ID，如 FUNC-001-01")
    name: str = Field(..., description="子功能名称")
    description: str = Field(..., description="子功能描述")
    priority: str = Field(..., description="优先级：P0|P1|P2")
    acceptance_criteria: List[str] = Field(
        default_factory=list, description="验收条件列表"
    )
    test_suggestions: List[TestSuggestion] = Field(
        default_factory=list, description="测试建议列表"
    )
    requirement_refs: List[dict] = Field(
        default_factory=list,
        description="关联的需求原文引用列表，每项含 section_title/paragraph_index/line_start/line_end/text/keywords",
    )


class FunctionNode(BaseModel):
    """一级功能节点（功能树的顶层）"""
    id: str = Field(..., description="功能 ID，如 FUNC-001")
    name: str = Field(..., description="功能名称")
    description: str = Field(..., description="功能描述")
    priority: str = Field(..., description="优先级：P0|P1|P2")
    dependencies: List[Dependency] = Field(
        default_factory=list, description="依赖关系列表"
    )
    sub_functions: List[SubFunction] = Field(
        default_factory=list, description="二级子功能列表"
    )


class AnalysisOverview(BaseModel):
    """分析概览统计"""
    product_name: str = Field(default="未知产品", description="产品名称")
    module_name: str = Field(default="未知模块", description="模块名称")
    total_functions: int = Field(default=0, description="功能总数")
    p0_count: int = Field(default=0, description="P0 功能数量")
    p1_count: int = Field(default=0, description="P1 功能数量")
    p2_count: int = Field(default=0, description="P2 功能数量")


class RequirementAnalysisResult(BaseModel):
    """需求分析完整结果"""
    overview: AnalysisOverview = Field(default_factory=AnalysisOverview)
    function_tree: List[FunctionNode] = Field(default_factory=list)


# =============================================================================
# 需求分析 Agent
# =============================================================================


class RequirementAnalyzer:
    """
    需求分析 Agent

    核心流程：
    1. 读取 Markdown 需求文档
    2. 从 RAG 知识库检索相关测试方法论
    3. 构建 Prompt（系统提示 + 方法论上下文 + 需求文档）
    4. 调用 LLM 进行分析
    5. 解析 LLM 输出为结构化 JSON
    6. 验证并保存结果

    Attributes:
        llm_model: 使用的 LLM 模型名称
        temperature: LLM 温度参数
        max_retries: LLM 调用最大重试次数
        kb: RAG 知识库实例
    """

    def __init__(
        self,
        llm_model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        max_retries: int = 2,
        kb_persist_dir: Optional[str] = None,
    ) -> None:
        """
        初始化需求分析 Agent

        Args:
            llm_model: LLM 模型名称
            temperature: 生成温度（低温度保证结构化输出稳定性）
            max_retries: LLM 调用失败时的最大重试次数
            kb_persist_dir: Chroma 持久化目录路径
        """
        self.llm_model = llm_model
        self.temperature = temperature
        self.max_retries = max_retries

        # LLM 延迟初始化（避免导入/测试时因缺少 API Key 而崩溃）
        self._llm: Optional[ChatOpenAI] = None

        # 初始化 RAG 知识库
        if kb_persist_dir is None:
            kb_persist_dir = os.path.join(_project_root, "chroma_db")
        self.kb = TestKnowledgeBase(persist_directory=kb_persist_dir)

    @property
    def llm(self) -> ChatOpenAI:
        """延迟初始化 LLM，仅在需要调用时创建"""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.llm_model,
                temperature=self.temperature,
                max_tokens=4096,
            )
        return self._llm

    def _read_prd(self, file_path: str) -> str:
        """
        读取 PRD Markdown 文档内容

        Args:
            file_path: PRD 文档路径

        Returns:
            文档文本内容

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件为空
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PRD 文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            raise ValueError(f"PRD 文件内容为空: {file_path}")

        return content

    def _retrieve_methodology(self, prd_content: str, n_results: int = 5) -> str:
        """
        从 RAG 知识库检索与需求相关的测试方法论

        使用 PRD 全文作为查询文本，获取语义相似的测试方法论文档块。

        Args:
            prd_content: PRD 文档全文
            n_results: 检索结果数量

        Returns:
            格式化的测试方法论文本，用于注入 Prompt
        """
        # 提取 PRD 关键段落作为查询（取前 1000 字符做摘要查询）
        query = prd_content[:1000]

        try:
            results = self.kb.search_methodology(query, n_results=n_results)
            if not results:
                return "（暂无相关测试方法论检索结果）"

            lines: List[str] = []
            for i, r in enumerate(results):
                score = r.get("score", 0)
                content = r.get("content", "")
                if content:
                    lines.append(f"### 参考资料 {i + 1}（相关度: {score:.2f}）\n{content}")

            return "\n\n".join(lines)
        except Exception as e:
            # 知识库检索失败不阻塞主流程
            return f"（知识库检索异常: {e}，将继续分析但无方法论参考）"

    def _build_prompt(
        self, prd_content: str, methodology_context: str
    ) -> ChatPromptTemplate:
        """
        构建 LangChain Prompt 模板

        注意：System Prompt 中包含 JSON 示例（含花括号），不能放入
        LangChain 模板变量系统（会与 f-string 语法冲突）。System Prompt
        直接拼接，Human Message 使用模板变量。

        Args:
            prd_content: PRD 文档全文
            methodology_context: 测试方法论上下文

        Returns:
            ChatPromptTemplate 实例
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        # 将方法论上下文注入 System Prompt（直接拼接，不经过模板引擎）
        system_prompt = REQUIREMENT_ANALYSIS_PROMPT.replace(
            "{methodology_context}", methodology_context
        )

        # Human 消息使用模板变量（仅此处需要变量替换）
        human_template = (
            "请分析以下需求文档，输出结构化的功能点分析 JSON：\n\n"
            "--- 需求文档开始 ---\n"
            "{prd_content}\n"
            "--- 需求文档结束 ---\n\n"
            "请严格按照 System Prompt 中定义的 JSON Schema 输出分析结果。"
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_template),
            ]
        )
        return prompt

    def _extract_json(self, raw_output: str) -> Dict[str, Any]:
        """
        从 LLM 原始输出中提取 JSON 对象

        处理 LLM 可能在 JSON 外包裹 Markdown 代码块的情况。

        Args:
            raw_output: LLM 原始输出文本

        Returns:
            解析后的 JSON 字典

        Raises:
            ValueError: 无法提取有效的 JSON
        """
        text = raw_output.strip()

        # 尝试提取 ```json ... ``` 代码块
        if "```json" in text:
            start = text.find("```json") + len("```json")
            end = text.find("```", start)
            if end > start:
                text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + len("```")
            end = text.find("```", start)
            if end > start:
                text = text[start:end].strip()

        # 尝试查找 JSON 对象起止位置
        json_start = text.find("{")
        json_end = text.rfind("}")
        if json_start >= 0 and json_end > json_start:
            text = text[json_start : json_end + 1]

        return json.loads(text)

    def analyze(self, prd_path: str) -> RequirementAnalysisResult:
        """
        分析需求文档，返回结构化功能点

        核心流程：
        读取 PRD → RAG 检索方法论 → 构建 Prompt → 调用 LLM → 解析 JSON → 验证

        Args:
            prd_path: PRD Markdown 文档的绝对路径

        Returns:
            RequirementAnalysisResult: 包含功能树和概览的结构化结果

        Raises:
            RuntimeError: LLM 调用在最大重试次数后仍失败
        """
        # 1. 读取 PRD
        prd_content = self._read_prd(prd_path)

        # 0. 无 API Key / 显式 Mock 模式 → 规则解析降级（保证流程可演示）
        mock_llm = os.getenv("ITEST_MOCK_LLM", "").lower() in ("1", "true", "yes")
        if mock_llm or not os.getenv("OPENAI_API_KEY"):
            print("[降级] 未配置 OPENAI_API_KEY 或启用 ITEST_MOCK_LLM，使用规则解析")
            result = rule_based_analyze(prd_content)
            self._enrich_requirement_refs(result, prd_path)
            return result

        # 2. RAG 检索方法论
        methodology_context = self._retrieve_methodology(prd_content)

        # 3. 构建 Prompt
        prompt = self._build_prompt(prd_content, methodology_context)

        # 4. 构建 Chain
        chain = prompt | self.llm | StrOutputParser()

        # 5. 调用 LLM（含重试逻辑）
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 2):  # +2 因为含初始尝试
            try:
                raw_output = chain.invoke({"prd_content": prd_content})
                parsed = self._extract_json(raw_output)

                # 使用 Pydantic 验证结构
                result = RequirementAnalysisResult(**parsed)
                return result

            except json.JSONDecodeError as e:
                last_error = e
                print(
                    f"[警告] 第 {attempt} 次尝试 JSON 解析失败: {e}，"
                    f"{'将重试...' if attempt <= self.max_retries else '已达最大重试次数'}"
                )
                if attempt <= self.max_retries:
                    time.sleep(2 ** attempt)  # 指数退避

            except Exception as e:
                last_error = e
                print(
                    f"[警告] 第 {attempt} 次尝试调用失败: {e}，"
                    f"{'将重试...' if attempt <= self.max_retries else '已达最大重试次数'}"
                )
                if attempt <= self.max_retries:
                    time.sleep(2 ** attempt)

        raise RuntimeError(
            f"LLM 调用在 {self.max_retries + 1} 次尝试后仍失败。"
            f"最后错误: {last_error}"
        )

    def _enrich_requirement_refs(
        self,
        result: RequirementAnalysisResult,
        prd_path: str,
    ) -> None:
        """
        为每个 SubFunction 填充 requirement_refs

        读取 PRD 全文，按行分割，用功能名称/描述关键词匹配相关段落，
        提取行号范围和原文引用。

        Args:
            result: 已分析完成的结果对象
            prd_path: PRD 文档路径
        """
        if not os.path.exists(prd_path):
            return

        try:
            with open(prd_path, "r", encoding="utf-8") as f:
                prd_content = f.read()
        except Exception:
            return

        lines = prd_content.split("\n")

        for func in result.function_tree:
            for sf in func.sub_functions:
                refs: List[dict] = []
                keywords = [sf.name]
                if sf.description:
                    # 取描述的前 20 个字符作为额外关键词
                    keywords.append(sf.description[:20])

                matched_indices: set = set()
                for i, line in enumerate(lines):
                    line_stripped = line.strip()
                    if not line_stripped:
                        continue
                    for kw in keywords:
                        if kw and kw in line_stripped:
                            matched_indices.add(i)
                            break

                # 合并连续行号，构建引用记录
                if matched_indices:
                    sorted_indices = sorted(matched_indices)
                    start_idx = sorted_indices[0]
                    end_idx = sorted_indices[0]

                    for idx in sorted_indices[1:]:
                        if idx == end_idx + 1:
                            end_idx = idx
                        else:
                            # 当前段落结束，保存上一段
                            text = "\n".join(
                                lines[start_idx : end_idx + 1]
                            )[:300]
                            refs.append({
                                "section_title": "",
                                "paragraph_index": start_idx,
                                "line_start": start_idx + 1,
                                "line_end": end_idx + 1,
                                "text": text,
                                "keywords": keywords[:],
                            })
                            start_idx = idx
                            end_idx = idx

                    # 保存最后一段
                    text = "\n".join(
                        lines[start_idx : end_idx + 1]
                    )[:300]
                    refs.append({
                        "section_title": "",
                        "paragraph_index": start_idx,
                        "line_start": start_idx + 1,
                        "line_end": end_idx + 1,
                        "text": text,
                        "keywords": keywords[:],
                    })

                sf.requirement_refs = refs

    def analyze_and_save(
        self, prd_path: str, output_path: Optional[str] = None
    ) -> str:
        """
        分析需求文档并将结果保存为 JSON 文件

        Args:
            prd_path: PRD Markdown 文档路径
            output_path: 输出 JSON 文件路径。若未指定，则与 PRD 同目录、
                        以 '_analysis_result.json' 后缀命名

        Returns:
            输出文件的绝对路径
        """
        result = self.analyze(prd_path)

        # 填充 requirement_refs
        self._enrich_requirement_refs(result, prd_path)

        # 确定输出路径
        if output_path is None:
            base = os.path.splitext(prd_path)[0]
            output_path = f"{base}_analysis_result.json"

        # 序列化并写入
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(
                result.model_dump(),
                f,
                ensure_ascii=False,
                indent=2,
            )

        print(f"分析完成！结果已保存至: {output_path}")
        print(f"  - 产品: {result.overview.product_name}")
        print(f"  - 模块: {result.overview.module_name}")
        print(f"  - 功能总数: {result.overview.total_functions}")
        print(f"  - P0: {result.overview.p0_count}, "
              f"P1: {result.overview.p1_count}, "
              f"P2: {result.overview.p2_count}")

        # 打印功能树摘要
        for fn in result.function_tree:
            print(f"  [{fn.id}] {fn.name} ({fn.priority})")
            for sf in fn.sub_functions:
                print(f"    └ {sf.id} {sf.name} ({sf.priority})")

        return output_path

    def get_kb_stats(self) -> Dict[str, int]:
        """获取知识库统计信息"""
        return self.kb.get_collection_stats()


# =============================================================================
# 独立运行入口：python -m agents.requirement_analyzer
# =============================================================================


def main() -> None:
    """命令行入口，验证模块可独立运行"""
    import argparse

    parser = argparse.ArgumentParser(
        description="iTest-Agent 需求分析器 — 从 PRD 文档提取结构化功能点"
    )
    parser.add_argument(
        "prd_path",
        nargs="?",
        default=None,
        help="PRD Markdown 文档路径（若不指定则使用默认样例 PRD）",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出 JSON 文件路径（默认与 PRD 同目录）",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="LLM 模型名称（默认 gpt-4o-mini）",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM 温度参数（默认 0.1）",
    )
    args = parser.parse_args()

    # 默认 PRD 路径
    prd_path = args.prd_path
    if prd_path is None:
        prd_path = os.path.join(_project_root, "tests", "sample_prd.md")

    if not os.path.exists(prd_path):
        print(f"错误: PRD 文件不存在: {prd_path}")
        sys.exit(1)

    # 初始化并执行
    analyzer = RequirementAnalyzer(
        llm_model=args.model,
        temperature=args.temperature,
    )

    stats = analyzer.get_kb_stats()
    print(f"知识库状态: 方法论={stats['methodology_count']}条, "
          f"用例库={stats['test_cases_count']}条")

    try:
        output_path = analyzer.analyze_and_save(prd_path, args.output)
        print(f"\n独立运行验证成功！输出文件: {output_path}")
    except Exception as e:
        print(f"\n分析失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
