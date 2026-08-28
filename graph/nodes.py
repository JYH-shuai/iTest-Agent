"""
iTest-Agent 工作流节点实现

每个节点是一个接受 AgentState 返回部分 AgentState 的函数。
节点间通过共享状态传递数据，LangGraph 自动合并返回的字段。

节点列表：
- analyze_requirements: 需求分析节点
- generate_testcases: 用例生成节点
- review_testcases: 用例评审节点
- execute_testcases: 用例执行节点
- generate_report: 报告生成节点
- handle_error: 错误处理节点（带重试逻辑）
- finalize: 流程终节点
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 添加项目根目录到 Python 路径
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from graph.state import (
    AgentState,
    ChangeType,
    ErrorInfo,
    ExecutionResult,
    ReviewResult,
    WorkflowPhase,
)

from utils.error_handler import (
    ErrorCategory,
    ErrorContext,
    ErrorRecoveryStrategy,
    classify_error,
    determine_recovery_strategy,
)
from utils.retry import RetryConfig, retry_on_failure


# =============================================================================
# 工具函数
# =============================================================================


def _add_message(state: AgentState, msg: str) -> None:
    """向状态追加工作流消息"""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    state.setdefault("messages", []).append(f"[{timestamp}] {msg}")


def _build_error_info(
    node_name: str,
    error: Exception,
    attempt: int,
    max_retries: int,
    phase: str = "unknown",
) -> ErrorInfo:
    """构建 ErrorInfo 字典"""
    return ErrorInfo(
        node=node_name,
        phase=phase,
        error_type=type(error).__name__,
        error_message=str(error),
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        attempt=attempt,
        max_retries=max_retries,
        recovered=False,
    )


def _is_incremental(state: AgentState) -> bool:
    """判断是否为增量更新模式"""
    ct = state.get("change_type", ChangeType.NONE.value)
    return ct in (
        ChangeType.INCREMENTAL.value,
        ChangeType.ADD_FUNCTION.value,
        ChangeType.REMOVE_FUNCTION.value,
    )


def _load_previous_analysis(state: AgentState) -> Optional[Dict]:
    """加载前次分析结果（增量更新时使用）"""
    change_log = state.get("change_log", [])
    if not change_log:
        return None
    prev_path = change_log[0].get("previous_analysis_path", "")
    if not prev_path or not os.path.exists(prev_path):
        return None
    try:
        with open(prev_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# =============================================================================
# 评审回退补强
# =============================================================================


def _enrich_suite_from_review(suite, review: Dict[str, Any]) -> None:
    """
    根据评审意见补充测试用例，使评审回退迭代收敛。

    当前补充策略（确定性规则）：
    - 缺少异常/边界场景 → 为每条已有用例补充"边界值验证"与"异常流程验证"用例
    - 用例缺少步骤 → 补充默认主流程步骤
    - 用例缺少前置条件 → 补充默认前置条件

    Args:
        suite: TestSuite 实例（原地修改）
        review: 评审结果字典（review_result）
    """
    from models.test_case import TestCase, TestStep

    gaps = " ".join(review.get("coverage_gaps", []) or []) + " " + str(
        review.get("feedback", "")
    )
    need_boundary_exception = (
        "异常" in gaps or "边界" in gaps or "边界值" in gaps
    )

    added = 0
    for tc in list(suite.test_cases):
        # 1. 缺少步骤 → 补充默认步骤
        if not tc.steps:
            tc.steps = [
                TestStep(step=1, action=f"执行「{tc.title}」主流程", expected="系统正常响应"),
            ]
            added += 1

        # 2. 缺少前置条件 → 补充默认前置条件
        if not tc.precondition or tc.precondition in ("无", "参照功能描述中的前置依赖"):
            tc.precondition = f"已进入「{tc.function_name or tc.function_id}」相关页面/接口环境"
            added += 1

        # 3. 缺少异常/边界用例 → 为每个功能补充两种场景用例（去重）
        if need_boundary_exception:
            existing_titles = {c.title for c in suite.test_cases}
            base = tc.title.replace(" - Happy Path 验证", "").replace(
                " - 正常流程验证", ""
            )
            boundary_title = f"{base} - 边界值验证"
            exception_title = f"{base} - 异常流程验证"

            if boundary_title not in existing_titles:
                suite.test_cases.append(
                    TestCase(
                        case_id=f"{tc.case_id}-BOUNDARY",
                        title=boundary_title,
                        function_id=tc.function_id,
                        function_name=tc.function_name,
                        type=tc.type,
                        priority=tc.priority,
                        precondition=tc.precondition,
                        test_data=tc.test_data,
                        steps=[
                            TestStep(
                                step=1,
                                action="输入边界值（最小值/最大值/临界值）",
                                expected="系统正确处理边界输入，无越界异常",
                            ),
                            TestStep(
                                step=2,
                                action="输入边界值附近的值（最小值-1/最大值+1）",
                                expected="系统给出明确提示或拒绝",
                            ),
                        ],
                        tags=["边界值"],
                        design_method="边界值分析",
                        traceability=tc.traceability,
                    )
                )
                added += 1

            if exception_title not in existing_titles:
                suite.test_cases.append(
                    TestCase(
                        case_id=f"{tc.case_id}-EXCEPTION",
                        title=exception_title,
                        function_id=tc.function_id,
                        function_name=tc.function_name,
                        type=tc.type,
                        priority=tc.priority,
                        precondition=tc.precondition,
                        test_data=tc.test_data,
                        steps=[
                            TestStep(
                                step=1,
                                action="输入非法/异常数据（空值、超长、格式错误）",
                                expected="系统提示明确错误信息，不崩溃",
                            ),
                            TestStep(
                                step=2,
                                action="重复提交相同请求",
                                expected="系统幂等处理或给出重复提示",
                            ),
                        ],
                        tags=["异常流程"],
                        design_method="异常场景分析",
                        traceability=tc.traceability,
                    )
                )
                added += 1

    suite.refresh_stats()
    if added:
        print(f"[生成] 根据评审意见补充 {added} 条用例/字段")


# =============================================================================
# 节点实现
# =============================================================================


def analyze_requirements(state: AgentState) -> AgentState:
    """
    需求分析节点

    调用 RequirementAnalyzer 对 PRD 文档进行分析，
    将结果摘要写入 state.analysis_result。

    增量模式：如果 change_type 为增量，仅对变更功能重新分析，
    其余功能从 previous_analysis 中继承。

    返回: 部分 AgentState（phase / analysis_result / error_occurred）
    """
    node_name = "analyze_requirements"
    _add_message(state, f"节点 {node_name} 开始执行")
    state["phase"] = WorkflowPhase.ANALYZING.value

    try:
        from agents.requirement_analyzer import RequirementAnalyzer

        prd_path = state.get("prd_path", "")
        if not prd_path or not os.path.exists(prd_path):
            raise FileNotFoundError(f"PRD 文件不存在: {prd_path}")

        analyzer = RequirementAnalyzer(
            llm_model=state.get("llm_model", "gpt-4o-mini"),
            temperature=state.get("config", {}).get("analyzer_temperature", 0.1),
            max_retries=state.get("max_retries", 2),
            kb_persist_dir=state.get("kb_persist_dir") or None,
            llm_api_key=state.get("llm_api_key") or "",
            llm_base_url=state.get("llm_base_url") or "",
            mock_llm=bool(state.get("mock_llm", False)),
        )

        # 确定输出路径
        output_dir = state.get("output_dir") or os.path.dirname(prd_path)
        os.makedirs(output_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(prd_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}_analysis_result.json")

        # 增量模式：检查是否需要全量重新分析
        if _is_incremental(state):
            changed_functions = state.get("change_log", [{}])[0].get("changed_functions", [])
            _add_message(
                state,
                f"增量更新模式 — 将仅处理变更功能: {changed_functions}",
            )
            # 先执行全量分析，后续在用例生成阶段按 changed_functions 过滤
            # （因为功能分解依赖完整 PRD 上下文，不易局部分析）

        # analyze_and_save 返回输出路径；重新加载 JSON 构建摘要
        saved_path = analyzer.analyze_and_save(prd_path, output_path)
        with open(saved_path, "r", encoding="utf-8") as f:
            analysis_data = json.load(f)
        overview = analysis_data.get("overview", {})

        analysis_summary = {
            "file_path": saved_path,
            "product_name": overview.get("product_name", ""),
            "module_name": overview.get("module_name", ""),
            "total_functions": overview.get("total_functions", 0),
            "p0_count": overview.get("p0_count", 0),
            "p1_count": overview.get("p1_count", 0),
            "p2_count": overview.get("p2_count", 0),
        }

        _add_message(
            state,
            f"需求分析完成 — {analysis_summary['total_functions']} 个功能点 "
            f"(P0:{analysis_summary['p0_count']} "
            f"P1:{analysis_summary['p1_count']} "
            f"P2:{analysis_summary['p2_count']})",
        )

        return AgentState(
            phase=WorkflowPhase.ANALYZING.value,
            analysis_result=analysis_summary,
            error_occurred=False,
            messages=state.get("messages", []),
        )

    except Exception as e:
        _add_message(state, f"需求分析失败: {e}")
        traceback.print_exc()
        return AgentState(
            phase=WorkflowPhase.ERROR.value,
            error_occurred=True,
            error_history=state.get("error_history", [])
            + [
                _build_error_info(
                    node_name, e, attempt=1, max_retries=state.get("max_retries", 2),
                    phase=state.get("phase", "unknown"),
                )
            ],
            messages=state.get("messages", []),
        )


def generate_testcases(state: AgentState) -> AgentState:
    """
    用例生成节点

    基于需求分析结果，调用 LLM 生成测试用例集。
    利用 models.test_case 中的 map_analysis_to_testsuite 构建骨架，
    再通过 LLM 填充具体测试步骤和数据。

    增量模式：仅对 changed_functions 中的功能重新生成用例。

    返回: 部分 AgentState（phase / test_suite / error_occurred）
    """
    node_name = "generate_testcases"
    _add_message(state, "节点 generate_testcases 开始执行")
    state["phase"] = WorkflowPhase.GENERATING.value
    generation_attempts = int(state.get("generation_attempts", 0)) + 1

    try:
        analysis = state.get("analysis_result", {})
        analysis_path = analysis.get("file_path", "")

        if not analysis_path or not os.path.exists(analysis_path):
            raise FileNotFoundError(f"需求分析结果不存在: {analysis_path}")

        # 读取分析结果
        with open(analysis_path, "r", encoding="utf-8") as f:
            analysis_data = json.load(f)

        # 构建 TestSuite
        from models.test_case import map_analysis_to_testsuite, TestSuite

        # 需要用 RequirementAnalysisResult 对象，手动重建
        from agents.requirement_analyzer import (
            RequirementAnalysisResult,
            AnalysisOverview,
            FunctionNode,
        )

        # 用 Pydantic 验证
        analysis_obj = RequirementAnalysisResult(**analysis_data)

        prd_path = state.get("prd_path", "")
        suite: TestSuite = map_analysis_to_testsuite(
            analysis_obj,
            requirement_source=os.path.basename(prd_path),
            requirement_path=prd_path,
        )

        # ── 评审回退：根据评审意见补充用例 ──
        review = state.get("review_result", {}) or {}
        if generation_attempts > 1 and not review.get("passed", True):
            _add_message(
                state,
                f"第 {generation_attempts} 次生成 — 根据评审意见补充边界/异常用例",
            )
            _enrich_suite_from_review(suite, review)

        # 增量模式：过滤只保留变更功能相关的用例
        if _is_incremental(state):
            changed_ids = state.get("change_log", [{}])[0].get("changed_functions", [])
            if changed_ids:
                original_count = len(suite.test_cases)
                suite.test_cases = [
                    tc for tc in suite.test_cases
                    if tc.function_id in changed_ids
                ]
                _add_message(
                    state,
                    f"增量模式 — 用例从 {original_count} 条过滤到 {len(suite.test_cases)} 条",
                )
            suite.refresh_stats()

        # 保存用例集
        output_dir = state.get("output_dir") or os.path.dirname(prd_path)
        os.makedirs(output_dir, exist_ok=True)
        suite_path = os.path.join(output_dir, "test_suite.json")

        with open(suite_path, "w", encoding="utf-8") as f:
            json.dump(
                suite.model_dump(), f, ensure_ascii=False, indent=2
            )

        # ── 构建双向追溯矩阵 ──
        traceability_matrix_path = ""
        try:
            # 从 analysis_data 中提取各个 SubFunction 的 requirement_refs
            function_tree: List[Dict[str, Any]] = []
            for func_node in analysis_data.get("function_tree", []):
                func_dict = func_node if isinstance(func_node, dict) else func_node.model_dump()
                for sf in func_dict.get("sub_functions", []):
                    sf_dict = sf if isinstance(sf, dict) else sf
                    sf_dict["_parent_function"] = {
                        "id": func_dict.get("id", ""),
                        "name": func_dict.get("name", ""),
                        "description": func_dict.get("description", ""),
                    }
                function_tree.append(func_dict)

            from models.traceability import TraceabilityMatrix

            matrix = TraceabilityMatrix()
            matrix.build_from_prd(
                prd_path=prd_path,
                function_tree=function_tree,
                test_suite=suite,
            )

            # 保存 JSON
            matrix_json_path = os.path.join(output_dir, "traceability_matrix.json")
            with open(matrix_json_path, "w", encoding="utf-8") as f:
                json.dump(matrix.to_dict(), f, ensure_ascii=False, indent=2)

            # 保存 Markdown
            matrix_md_path = os.path.join(output_dir, "traceability_matrix.md")
            with open(matrix_md_path, "w", encoding="utf-8") as f:
                f.write(matrix.to_markdown())

            traceability_matrix_path = matrix_json_path
            _add_message(
                state,
                f"追溯矩阵已生成 — JSON: {matrix_json_path}, Markdown: {matrix_md_path}",
            )
        except Exception as e:
            _add_message(state, f"追溯矩阵构建失败（不影响主流程）: {e}")
            traceback.print_exc()

        suite_info = {
            "file_path": suite_path,
            "suite_name": suite.suite_name,
            "total_cases": suite.total_cases,
            "p0_count": suite.p0_count,
            "p1_count": suite.p1_count,
            "p2_count": suite.p2_count,
            "traceability_matrix_path": traceability_matrix_path,
        }

        _add_message(
            state,
            f"用例生成完成 — {suite_info['total_cases']} 条用例 "
            f"(P0:{suite_info['p0_count']} "
            f"P1:{suite_info['p1_count']} "
            f"P2:{suite_info['p2_count']})",
        )

        return AgentState(
            phase=WorkflowPhase.GENERATING.value,
            test_suite=suite_info,
            error_occurred=False,
            generation_attempts=generation_attempts,
            messages=state.get("messages", []),
        )

    except Exception as e:
        _add_message(state, f"用例生成失败: {e}")
        traceback.print_exc()
        return AgentState(
            phase=WorkflowPhase.ERROR.value,
            error_occurred=True,
            error_history=state.get("error_history", [])
            + [
                _build_error_info(
                    node_name, e, attempt=1, max_retries=state.get("max_retries", 2),
                    phase=state.get("phase", "unknown"),
                )
            ],
            generation_attempts=generation_attempts,
            messages=state.get("messages", []),
        )


def review_testcases(state: AgentState) -> AgentState:
    """
    用例评审节点

    对生成的测试用例进行质量评审：
    - 检查覆盖度（功能覆盖率）
    - 检查边界值和异常流程是否充分
    - 输出评审分数和未通过用例列表

    评审通过阈值: score >= 70.0

    返回: 部分 AgentState（phase / review_result / error_occurred）
    """
    node_name = "review_testcases"
    _add_message(state, "节点 review_testcases 开始执行")
    state["phase"] = WorkflowPhase.REVIEWING.value

    try:
        suite_path = state.get("test_suite", {}).get("file_path", "")
        if not suite_path or not os.path.exists(suite_path):
            raise FileNotFoundError(f"用例集文件不存在: {suite_path}")

        with open(suite_path, "r", encoding="utf-8") as f:
            suite_data = json.load(f)

        test_cases = suite_data.get("test_cases", [])
        total = len(test_cases)

        if total == 0:
            review = ReviewResult(
                passed=True,
                score=100.0,
                total_cases=0,
                passed_cases=0,
                failed_cases=0,
                feedback="无用例需要评审",
                failed_case_ids=[],
                coverage_gaps=[],
            )
        else:
            # 评审规则：
            # 1. 每条用例必须有步骤（steps 非空）→ 否则不合格
            # 2. 每条用例必须有前置条件（precondition 不为空/无） → 否则扣分
            # 3. 如果没有 P0 用例但功能有 P0 → 覆盖度缺口
            # 4. 边界值/异常流程用例应 >= 总用例的 30%

            failed_case_ids: List[str] = []
            missing_precond = 0
            empty_steps = 0

            for tc in test_cases:
                cid = tc.get("case_id", "")
                steps = tc.get("steps", [])
                precondition = tc.get("precondition", "")

                if not steps:
                    empty_steps += 1
                    if cid not in failed_case_ids:
                        failed_case_ids.append(cid)
                if not precondition or precondition == "无":
                    missing_precond += 1

            # 评分计算
            base_score = 100.0
            if total > 0:
                base_score -= (empty_steps / total) * 30   # 缺少步骤：-30%
                base_score -= (missing_precond / total) * 10  # 缺少前置条件：-10%

            # 检查 P0 覆盖
            analysis = state.get("analysis_result", {})
            coverage_gaps: List[str] = []
            if analysis.get("p0_count", 0) > 0:
                p0_cases = [tc for tc in test_cases if tc.get("priority") == "P0"]
                if len(p0_cases) == 0:
                    coverage_gaps.append("缺少P0优先级用例覆盖")
                    base_score -= 20

            # 边界值/异常覆盖检查
            exception_count = sum(
                1
                for tc in test_cases
                if "异常" in tc.get("title", "") or "边界" in tc.get("title", "")
            )
            exception_ratio = exception_count / total if total > 0 else 0.0
            hard_fail = False
            if total > 0 and exception_ratio < 0.3:
                coverage_gaps.append(
                    f"边界值/异常流程用例占比不足30%（当前 {exception_ratio:.0%}）"
                )
                base_score -= 10
                # 硬性门槛：异常/边界覆盖不足 30% 视为不通过，触发回退补充
                hard_fail = True

            passed = base_score >= 70.0 and not hard_fail

            review = ReviewResult(
                passed=passed,
                score=max(0.0, min(100.0, base_score)),
                total_cases=total,
                passed_cases=total - len(failed_case_ids),
                failed_cases=len(failed_case_ids),
                feedback=(
                    f"评审{'通过' if passed else '不通过'}。"
                    f"缺少步骤: {empty_steps} 条，缺少前置条件: {missing_precond} 条。"
                    f"覆盖度缺口: {', '.join(coverage_gaps) if coverage_gaps else '无'}"
                ),
                failed_case_ids=failed_case_ids,
                coverage_gaps=coverage_gaps,
            )

        # 保存评审结果
        output_dir = state.get("output_dir") or os.path.dirname(
            state.get("prd_path", ".")
        )
        review_path = os.path.join(output_dir, "review_result.json")
        with open(review_path, "w", encoding="utf-8") as f:
            json.dump(dict(review), f, ensure_ascii=False, indent=2)

        # ── 评审迭代上限：连续多次不通过则终止，防止无限回退 ──
        cfg = state.get("config", {}) or {}
        max_rounds = int(cfg.get("max_review_rounds", 3))
        attempts = int(state.get("generation_attempts", 0))
        if not review["passed"] and attempts >= max_rounds:
            _add_message(
                state,
                f"评审连续 {attempts} 次未通过，停止迭代（上限 {max_rounds}）",
            )
            return AgentState(
                phase=WorkflowPhase.ERROR.value,
                review_result=review,
                error_occurred=True,
                error_history=state.get("error_history", [])
                + [
                    {
                        "node": node_name,
                        "phase": WorkflowPhase.REVIEWING.value,
                        "error_type": "ReviewRetryExhausted",
                        "error_message": (
                            f"用例评审连续 {attempts} 次未通过（上限 {max_rounds}），"
                            f"未通过原因: {review['feedback']}"
                        ),
                        "timestamp": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "attempt": attempts,
                        "max_retries": max_rounds,
                        "recovered": False,
                    }
                ],
                messages=state.get("messages", []),
            )

        _add_message(
            state,
            f"用例评审完成 — 评分: {review['score']:.1f}, "
            f"{'通过' if review['passed'] else '不通过'}, "
            f"未通过: {review['failed_cases']} 条",
        )

        return AgentState(
            phase=WorkflowPhase.REVIEWING.value,
            review_result=review,
            error_occurred=False,
            messages=state.get("messages", []),
        )

    except Exception as e:
        _add_message(state, f"用例评审失败: {e}")
        traceback.print_exc()
        return AgentState(
            phase=WorkflowPhase.ERROR.value,
            error_occurred=True,
            error_history=state.get("error_history", [])
            + [
                _build_error_info(
                    node_name, e, attempt=1, max_retries=state.get("max_retries", 2),
                    phase=state.get("phase", "unknown"),
                )
            ],
            messages=state.get("messages", []),
        )


def execute_testcases(state: AgentState) -> AgentState:
    """
    用例执行节点

    读取评审通过的测试用例集，逐条执行。
    每条用例独立重试（使用 retry_on_failure 装饰器），
    单条失败不影响整体，错误分类决定跳过/重试/中止。

    执行模式：
    - mcp: 通过 MCP 协议调用自研 Playwright / API Test MCP Server 真实执行
    - simulated: 模拟执行（默认，保证无工具环境下全流程可跑通）
    配置：state.config.execution_mode 或环境变量 ITEST_EXECUTION_MODE。

    返回: 部分 AgentState（phase / execution_result / error_occurred）
    """
    node_name = "execute_testcases"
    _add_message(state, "节点 execute_testcases 开始执行")
    state["phase"] = WorkflowPhase.EXECUTING.value

    try:
        suite_path = state.get("test_suite", {}).get("file_path", "")
        if not suite_path or not os.path.exists(suite_path):
            raise FileNotFoundError(f"用例集文件不存在: {suite_path}")

        with open(suite_path, "r", encoding="utf-8") as f:
            suite_data = json.load(f)

        test_cases = suite_data.get("test_cases", [])
        total = len(test_cases)

        if total == 0:
            _add_message(state, "无用例可执行")
            return AgentState(
                phase=WorkflowPhase.EXECUTING.value,
                execution_result=ExecutionResult(
                    total=0, passed=0, failed=0, blocked=0, skipped=0,
                    pass_rate=0.0, duration_seconds=0.0, log_path="",
                ),
                error_occurred=False,
                messages=state.get("messages", []),
            )

        # ── 逐条执行（带重试）──
        passed = 0
        failed = 0
        blocked = 0
        skipped = 0
        start_time = time.time()
        execution_details: List[Dict[str, Any]] = []

        retry_cfg = RetryConfig(
            max_retries=state.get("max_retries", 2),
            base_delay=0.5,
            jitter=True,
        )

        # ── 初始化执行引擎 ──
        cfg = state.get("config", {}) or {}
        exec_mode = (
            cfg.get("execution_mode")
            or os.getenv("ITEST_EXECUTION_MODE", "simulated")
        )
        from execution.engine import ExecutionEngine

        engine = ExecutionEngine(
            mode=exec_mode,
            api_base_url=os.getenv("ITEST_API_BASE_URL", ""),
            request_timeout=float(os.getenv("ITEST_EXECUTION_TIMEOUT", "30")),
        )

        for tc in test_cases:
            case_id = tc.get("case_id", "unknown")
            case_title = tc.get("title", "untitled")
            tc_data = dict(tc)

            @retry_on_failure(config=retry_cfg)
            def _run_case(
                cid: str = case_id,
                title: str = case_title,
                data: Dict[str, Any] = tc_data,
            ) -> Dict[str, Any]:
                """执行单条用例（MCP 真实执行或模拟降级）"""
                _add_message(state, f"  [执行] {cid}: {title}")
                result = engine.execute_case(data)
                if result.get("status") == "failed":
                    # 断言失败视为可重试（防抖），交给 retry_on_failure
                    raise AssertionError(
                        f"用例执行失败: {result.get('details', {})}"
                    )
                return result

            try:
                case_result = _run_case()
                status = case_result.get("status", "passed")
                if status == "passed":
                    passed += 1
                elif status == "blocked":
                    blocked += 1
                else:
                    failed += 1
                execution_details.append(case_result)
            except RuntimeError as e:
                # 重试耗尽 → 标记失败
                _add_message(state, f"  [FAILED] {case_id}: {case_title} — 重试耗尽")
                failed += 1
                execution_details.append({
                    "case_id": case_id,
                    "title": case_title,
                    "status": "failed",
                    "error": str(e),
                })
            except AssertionError as e:
                # 断言失败（重试后仍失败）→ 标记失败
                _add_message(state, f"  [FAILED] {case_id}: {case_title} — 断言失败")
                failed += 1
                execution_details.append({
                    "case_id": case_id,
                    "title": case_title,
                    "status": "failed",
                    "error": str(e),
                })

        duration = round(time.time() - start_time, 2)
        total_executed = passed + failed + blocked
        pass_rate = (passed / total) * 100 if total > 0 else 0.0

        # ── 保存执行日志 ──
        output_dir = state.get("output_dir") or os.path.dirname(
            state.get("prd_path", ".")
        )
        log_path = os.path.join(output_dir, "execution_log.json")
        log_data = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "skipped": skipped,
            "pass_rate": round(pass_rate, 1),
            "duration_seconds": duration,
            "details": execution_details,
            "execution_mode": exec_mode,
            "tool_calls": engine.tool_log,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)

        execution = ExecutionResult(
            total=total,
            passed=passed,
            failed=failed,
            blocked=blocked,
            skipped=skipped,
            pass_rate=pass_rate / 100.0,
            duration_seconds=duration,
            log_path=log_path,
            execution_mode=exec_mode,
        )

        _add_message(
            state,
            f"用例执行完成 — {total} 条，通过:{passed} 失败:{failed} "
            f"阻断:{blocked} 通过率:{pass_rate:.1f}% 耗时:{duration}s",
        )

        return AgentState(
            phase=WorkflowPhase.EXECUTING.value,
            execution_result=execution,
            error_occurred=False,
            messages=state.get("messages", []),
        )

    except Exception as e:
        _add_message(state, f"用例执行失败: {e}")
        traceback.print_exc()
        return AgentState(
            phase=WorkflowPhase.ERROR.value,
            error_occurred=True,
            error_history=state.get("error_history", [])
            + [
                _build_error_info(
                    node_name, e, attempt=1, max_retries=state.get("max_retries", 2),
                    phase=state.get("phase", "unknown"),
                )
            ],
            messages=state.get("messages", []),
        )


def generate_report(state: AgentState) -> AgentState:
    """
    报告生成节点

    汇总所有阶段结果，生成测试报告（Markdown / JSON）。
    报告包含：需求概览、用例统计、执行结果、评审意见。

    返回: 部分 AgentState（phase / report_path / error_occurred）
    """
    node_name = "generate_report"
    _add_message(state, "节点 generate_report 开始执行")
    state["phase"] = WorkflowPhase.REPORTING.value

    try:
        output_dir = state.get("output_dir") or os.path.dirname(
            state.get("prd_path", ".")
        )
        os.makedirs(output_dir, exist_ok=True)

        analysis = state.get("analysis_result", {})
        suite = state.get("test_suite", {})
        review = state.get("review_result", {})
        execution = state.get("execution_result", {})

        # 生成 Markdown 报告
        report_lines = [
            "# iTest-Agent 测试报告",
            "",
            f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
            "",
            "## 1. 需求概览",
            f"- 产品: {analysis.get('product_name', 'N/A')}",
            f"- 模块: {analysis.get('module_name', 'N/A')}",
            f"- 功能总数: {analysis.get('total_functions', 0)}",
            f"- 优先级分布: P0={analysis.get('p0_count', 0)}, "
            f"P1={analysis.get('p1_count', 0)}, P2={analysis.get('p2_count', 0)}",
            "",
            "## 2. 测试用例统计",
            f"- 用例总数: {suite.get('total_cases', 0)}",
            f"- P0: {suite.get('p0_count', 0)}",
            f"- P1: {suite.get('p1_count', 0)}",
            f"- P2: {suite.get('p2_count', 0)}",
            "",
            "## 3. 用例评审",
            f"- 评审结果: {'通过' if review.get('passed') else '不通过'}",
            f"- 评分: {review.get('score', 'N/A')}",
            f"- 未通过用例: {review.get('failed_cases', 0)} 条",
        ]

        if review.get("feedback"):
            report_lines.append(f"- 评审意见: {review['feedback']}")
        if review.get("coverage_gaps"):
            report_lines.append(
                f"- 覆盖度缺口: {', '.join(review['coverage_gaps'])}"
            )

        report_lines.extend([
            "",
            "## 4. 执行结果",
            f"- 总用例: {execution.get('total', 0)}",
            f"- 通过: {execution.get('passed', 0)}",
            f"- 失败: {execution.get('failed', 0)}",
            f"- 阻塞: {execution.get('blocked', 0)}",
            f"- 跳过: {execution.get('skipped', 0)}",
            f"- 通过率: {execution.get('pass_rate', 0):.1%}",
            "",
            "## 5. 工作流日志",
        ])

        # 追加消息日志
        for msg in state.get("messages", []):
            report_lines.append(f"- {msg}")

        report_md = "\n".join(report_lines)

        report_path = os.path.join(output_dir, "test_report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        _add_message(state, f"测试报告已生成: {report_path}")

        return AgentState(
            phase=WorkflowPhase.COMPLETED.value,
            report_path=report_path,
            error_occurred=False,
            messages=state.get("messages", []),
        )

    except Exception as e:
        _add_message(state, f"报告生成失败: {e}")
        traceback.print_exc()
        return AgentState(
            phase=WorkflowPhase.ERROR.value,
            error_occurred=True,
            error_history=state.get("error_history", [])
            + [
                _build_error_info(
                    node_name, e, attempt=1, max_retries=state.get("max_retries", 2),
                    phase=state.get("phase", "unknown"),
                )
            ],
            messages=state.get("messages", []),
        )


def handle_error(state: AgentState) -> AgentState:
    """
    错误处理节点

    使用 utils.error_handler 对最近一次错误进行分类，
    根据分类结果决定后续路由策略：

    - RETRYABLE（可重试）→ 尝试次数未耗尽时回到 INIT 重试
    - FATAL（致命）→ 标记流程取消
    - DEGRADED（降级）→ 根据节点决定 SKIP_NODE 或 FALLBACK

    返回: 部分 AgentState（phase / error_history）
    """
    node_name = "handle_error"
    _add_message(state, f"进入错误处理节点 — 当前阶段: {state.get('phase')}")

    error_history: List[Dict] = list(state.get("error_history", []))
    max_retries = state.get("max_retries", 2)

    # 找到最近未恢复的错误
    unrecovered = [e for e in error_history if not e.get("recovered", False)]
    if not unrecovered:
        _add_message(state, "无未恢复的错误，继续执行")
        return AgentState(
            phase=WorkflowPhase.INIT.value,
            error_occurred=False,
            messages=state.get("messages", []),
        )

    latest_error = unrecovered[-1]
    current_attempt = latest_error.get("attempt", 1)
    error_node_name = latest_error.get("node", "unknown")
    error_type_name = latest_error.get("error_type", "Exception")

    # ── 评审迭代上限：直接取消（防无限回退） ──
    if error_type_name == "ReviewRetryExhausted":
        _add_message(
            state,
            f"节点 {error_node_name} 评审迭代上限已耗尽，工作流取消",
        )
        return AgentState(
            phase=WorkflowPhase.CANCELLED.value,
            error_occurred=True,
            error_history=error_history,
            messages=state.get("messages", []),
        )

    # ── 同一节点反复失败：次数耗尽 → 取消（防止持久性错误无限重试） ──
    node_fail_count = sum(
        1
        for e in error_history
        if e.get("node") == error_node_name and not e.get("recovered", False)
    )
    if node_fail_count > max_retries:
        _add_message(
            state,
            f"节点 {error_node_name} 已失败 {node_fail_count} 次"
            f"（上限 {max_retries}），工作流取消",
        )
        return AgentState(
            phase=WorkflowPhase.CANCELLED.value,
            error_occurred=True,
            error_history=error_history,
            messages=state.get("messages", []),
        )

    # ── 构建 ErrorContext 并分类 ──
    err_ctx = ErrorContext(
        node_name=error_node_name,
        phase=latest_error.get("phase", state.get("phase", "unknown")),
        attempt=current_attempt,
        max_retries=max_retries,
        exception=None,  # 不传原始异常，走文字分类
    )

    category = classify_error(err_ctx)
    strategy = determine_recovery_strategy(category, err_ctx)

    _add_message(
        state,
        f"错误分类: [{error_type_name}] → {category.value}，"
        f"恢复策略: {strategy.value}",
    )

    # ── 按策略执行 ──
    if strategy == ErrorRecoveryStrategy.RETRY:
        # 可重试：更新 attempt 计数
        latest_error_copy = dict(latest_error)
        latest_error_copy["attempt"] = current_attempt + 1
        for i, e in enumerate(error_history):
            if e.get("timestamp") == latest_error.get("timestamp"):
                error_history[i] = latest_error_copy
                break

        _add_message(
            state,
            f"节点 {error_node_name} 第 {current_attempt}/{max_retries} 次重试",
        )
        return AgentState(
            phase=WorkflowPhase.INIT.value,
            error_occurred=False,
            error_history=error_history,
            messages=state.get("messages", []),
        )

    elif strategy == ErrorRecoveryStrategy.SKIP_NODE:
        # 跳过当前节点，标记为已恢复，继续后续流程
        latest_error_copy = dict(latest_error)
        latest_error_copy["recovered"] = True
        for i, e in enumerate(error_history):
            if e.get("timestamp") == latest_error.get("timestamp"):
                error_history[i] = latest_error_copy
                break

        _add_message(
            state,
            f"节点 {error_node_name} 降级处理 — 跳过该节点继续执行",
        )
        return AgentState(
            phase=WorkflowPhase.INIT.value,
            error_occurred=False,
            error_history=error_history,
            messages=state.get("messages", []),
        )

    elif strategy == ErrorRecoveryStrategy.FALLBACK:
        # 降级路径：走 fallback 逻辑
        latest_error_copy = dict(latest_error)
        latest_error_copy["recovered"] = True
        for i, e in enumerate(error_history):
            if e.get("timestamp") == latest_error.get("timestamp"):
                error_history[i] = latest_error_copy
                break

        _add_message(
            state,
            f"节点 {error_node_name} 走降级路径（fallback）",
        )
        return AgentState(
            phase=WorkflowPhase.INIT.value,
            error_occurred=False,
            error_history=error_history,
            messages=state.get("messages", []),
        )

    else:
        # ABORT 或其他 → 取消工作流
        latest_error_copy = dict(latest_error)
        latest_error_copy["recovered"] = False
        for i, e in enumerate(error_history):
            if e.get("timestamp") == latest_error.get("timestamp"):
                error_history[i] = latest_error_copy
                break

        _add_message(
            state,
            f"节点 {error_node_name} 重试已耗尽或致命错误，"
            f"工作流取消（分类: {category.value}）",
        )
        return AgentState(
            phase=WorkflowPhase.CANCELLED.value,
            error_occurred=True,
            error_history=error_history,
            messages=state.get("messages", []),
        )


def finalize(state: AgentState) -> AgentState:
    """
    工作流终点节点

    清理临时状态、输出最终摘要。
    """
    _add_message(state, f"工作流结束 — 最终阶段: {state.get('phase')}")
    return AgentState(
        phase=WorkflowPhase.COMPLETED.value,
        error_occurred=False,
        messages=state.get("messages", []),
    )
