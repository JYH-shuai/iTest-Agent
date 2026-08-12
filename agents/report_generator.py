"""
iTest-Agent 报告生成 Agent — 测试报告 Markdown / PDF 导出模块

输入：用例执行结果（execution_log.json） + 用例集（test_suite.json）
输出：结构化测试报告（Markdown + PDF）

特性：
- 测试摘要（总数 / 通过 / 失败 / 跳过 / 阻塞）
- 通过率计算与可视化（进度条）
- 缺陷聚类（按严重程度/功能模块/用例类型分组）
- 详细失败用例列表（含步骤信息）
- Markdown 格式导出（优化排版，适合版本管理）
- PDF 格式导出（基于 markdown + weasyprint，美观排版）
- 独立可运行：python -m agents.report_generator
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# 添加项目根目录到 Python 路径（支持独立运行）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# =============================================================================
# 报告数据模型
# =============================================================================


class ReportData:
    """报告数据结构，统一管理所有报告所需数据"""

    def __init__(
        self,
        execution_log: Dict[str, Any],
        test_suite: Optional[Dict[str, Any]] = None,
        analysis_result: Optional[Dict[str, Any]] = None,
        review_result: Optional[Dict[str, Any]] = None,
    ):
        self.execution = execution_log
        self.suite = test_suite or {}
        self.analysis = analysis_result or {}
        self.review = review_result or {}

        # 执行统计
        self.total: int = execution_log.get("total", 0)
        self.passed: int = execution_log.get("passed", 0)
        self.failed: int = execution_log.get("failed", 0)
        self.blocked: int = execution_log.get("blocked", 0)
        self.skipped: int = execution_log.get("skipped", 0)
        self.pass_rate: float = execution_log.get("pass_rate", 0.0)
        self.duration: float = execution_log.get("duration_seconds", 0.0)
        self.timestamp: str = execution_log.get("timestamp", "")

        # 执行明细
        self.details: List[Dict] = execution_log.get("details", [])

        # 用例集索引：case_id → TestCase
        self._case_index: Dict[str, Dict] = {}
        if self.suite:
            for tc in self.suite.get("test_cases", []):
                self._case_index[tc.get("case_id", "")] = tc

    def get_case(self, case_id: str) -> Optional[Dict]:
        """根据 case_id 获取用例详情"""
        return self._case_index.get(case_id)

    @property
    def passed_pct(self) -> float:
        """通过百分比"""
        return (self.passed / self.total * 100) if self.total > 0 else 0.0

    @property
    def failed_pct(self) -> float:
        """失败百分比"""
        return (self.failed / self.total * 100) if self.total > 0 else 0.0

    @property
    def blocked_pct(self) -> float:
        return (self.blocked / self.total * 100) if self.total > 0 else 0.0

    @property
    def skipped_pct(self) -> float:
        return (self.skipped / self.total * 100) if self.total > 0 else 0.0


# =============================================================================
# 缺陷聚类分析
# =============================================================================


class DefectClusterer:
    """缺陷聚类分析器：按严重程度/功能模块/用例类型分组"""

    @staticmethod
    def by_priority(data: ReportData) -> Dict[str, Dict[str, Any]]:
        """
        按优先级（严重程度）聚类失败用例

        Returns:
            {"P0": {"count": N, "cases": [...], "pct": X}, ...}
        """
        clusters: Dict[str, Dict[str, Any]] = {
            "P0": {"count": 0, "cases": [], "label": "严重（P0）"},
            "P1": {"count": 0, "cases": [], "label": "重要（P1）"},
            "P2": {"count": 0, "cases": [], "label": "一般（P2）"},
            "unknown": {"count": 0, "cases": [], "label": "未分类"},
        }

        for detail in data.details:
            status = detail.get("status", "")
            if status not in ("failed", "blocked"):
                continue

            case_id = detail.get("case_id", "")
            case = data.get_case(case_id)
            priority = case.get("priority", "unknown") if case else "unknown"

            if priority in clusters:
                clusters[priority]["count"] += 1
            else:
                clusters["unknown"]["count"] += 1

            clusters[priority]["cases"].append({
                "case_id": case_id,
                "title": case.get("title", "") if case else detail.get("title", ""),
                "error": detail.get("error", ""),
                "function_name": case.get("function_name", "") if case else "",
            })

        total_failed = sum(c["count"] for c in clusters.values())
        for key in clusters:
            c = clusters[key]
            c["pct"] = round(c["count"] / total_failed * 100, 1) if total_failed > 0 else 0.0

        return clusters

    @staticmethod
    def by_module(data: ReportData) -> Dict[str, int]:
        """按功能模块聚类失败/阻塞用例"""
        module_counts: Dict[str, int] = {}
        for detail in data.details:
            status = detail.get("status", "")
            if status not in ("failed", "blocked"):
                continue
            case = data.get_case(detail.get("case_id", ""))
            mod = case.get("function_name", "未知模块") if case else "未知模块"
            module_counts[mod] = module_counts.get(mod, 0) + 1
        return dict(sorted(module_counts.items(), key=lambda x: x[1], reverse=True))

    @staticmethod
    def by_type(data: ReportData) -> Dict[str, int]:
        """按用例类型聚类失败/阻塞用例"""
        type_counts: Dict[str, int] = {}
        for detail in data.details:
            status = detail.get("status", "")
            if status not in ("failed", "blocked"):
                continue
            case = data.get_case(detail.get("case_id", ""))
            typ = case.get("type", "未知类型") if case else "未知类型"
            type_counts[typ] = type_counts.get(typ, 0) + 1
        return dict(sorted(type_counts.items(), key=lambda x: x[1], reverse=True))


# =============================================================================
# Markdown 报告生成器
# =============================================================================


class MarkdownReportBuilder:
    """生成美观的 Markdown 测试报告"""

    @staticmethod
    def _progress_bar(pct: float, width: int = 20) -> str:
        """生成文本进度条"""
        filled = int(pct / 100 * width)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return f"`{bar}` {pct:.1f}%"

    @staticmethod
    def build(data: ReportData) -> str:
        """构建完整的 Markdown 报告"""
        lines: List[str] = []
        clusterer = DefectClusterer()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # ── 标题与元信息 ──
        product = data.analysis.get("product_name", "") or data.suite.get("product_name", "未知产品")
        module = data.analysis.get("module_name", "") or data.suite.get("module_name", "未知模块")

        lines.append(f"# iTest-Agent 测试报告")
        lines.append("")
        lines.append(f"**产品/模块**: {product} / {module}")
        lines.append(f"**报告生成时间**: {now_str}")
        if data.timestamp:
            lines.append(f"**测试执行时间**: {data.timestamp}")
        if data.duration > 0:
            lines.append(f"**执行耗时**: {data.duration:.1f}s")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── 1. 测试摘要 ──
        lines.append("## 1. 测试摘要")
        lines.append("")
        lines.append(f"| 指标 | 数值 | 占比 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| **用例总数** | {data.total} | 100% |")
        lines.append(f"| **通过** | {data.passed} | {data.passed_pct:.1f}% |")
        lines.append(f"| **失败** | {data.failed} | {data.failed_pct:.1f}% |")
        lines.append(f"| **阻塞** | {data.blocked} | {data.blocked_pct:.1f}% |")
        lines.append(f"| **跳过** | {data.skipped} | {data.skipped_pct:.1f}% |")
        lines.append("")

        # 通过率
        lines.append(f"### 通过率: {data.passed_pct:.1f}%")
        lines.append("")
        lines.append(MarkdownReportBuilder._progress_bar(data.passed_pct))
        lines.append("")

        # ── 2. 缺陷聚类 ──
        lines.append("---")
        lines.append("")
        lines.append("## 2. 缺陷聚类分析")
        lines.append("")

        total_failed = data.failed + data.blocked
        if total_failed == 0:
            lines.append("> 无失败或阻塞用例，无需缺陷聚类。")
            lines.append("")
        else:
            # 2.1 按严重程度
            lines.append("### 2.1 按严重程度分组")
            lines.append("")
            priority_clusters = clusterer.by_priority(data)
            lines.append(f"| 严重程度 | 缺陷数 | 占比 |")
            lines.append(f"|----------|--------|------|")
            for key in ("P0", "P1", "P2", "unknown"):
                c = priority_clusters[key]
                if c["count"] > 0:
                    lines.append(f"| {c['label']} | {c['count']} | {c['pct']}% |")
            lines.append("")

            # 2.2 按功能模块
            lines.append("### 2.2 按功能模块分组")
            lines.append("")
            module_clusters = clusterer.by_module(data)
            if module_clusters:
                lines.append(f"| 功能模块 | 缺陷数 |")
                lines.append(f"|----------|--------|")
                for mod, cnt in module_clusters.items():
                    lines.append(f"| {mod} | {cnt} |")
                lines.append("")
            else:
                lines.append("> 无法获取模块信息。")
                lines.append("")

            # 2.3 按用例类型
            lines.append("### 2.3 按用例类型分组")
            lines.append("")
            type_clusters = clusterer.by_type(data)
            if type_clusters:
                lines.append(f"| 用例类型 | 缺陷数 |")
                lines.append(f"|----------|--------|")
                for typ, cnt in type_clusters.items():
                    lines.append(f"| {typ} | {cnt} |")
                lines.append("")
            else:
                lines.append("> 无法获取类型信息。")
                lines.append("")

        # ── 3. 详细失败用例列表 ──
        lines.append("---")
        lines.append("")
        lines.append("## 3. 详细失败与阻塞用例")
        lines.append("")

        failed_details = [
            d for d in data.details if d.get("status") in ("failed", "blocked")
        ]

        if not failed_details:
            lines.append("> 无失败或阻塞用例。")
            lines.append("")
        else:
            for i, detail in enumerate(failed_details, 1):
                case_id = detail.get("case_id", "N/A")
                case = data.get_case(case_id)
                title = case.get("title", "") if case else detail.get("title", "N/A")
                status = detail.get("status", "failed")
                status_badge = "🔴 失败" if status == "failed" else "🟡 阻塞"
                priority = case.get("priority", "N/A") if case else "N/A"
                func_name = case.get("function_name", "") if case else ""
                error_msg = detail.get("error", "无错误详情")

                lines.append(f"### {i}. [{priority}] {title}")
                lines.append("")
                lines.append(f"- **用例ID**: `{case_id}`")
                lines.append(f"- **状态**: {status_badge}")
                lines.append(f"- **优先级**: {priority}")
                if func_name:
                    lines.append(f"- **关联功能**: {func_name}")

                # 测试步骤
                if case and case.get("steps"):
                    lines.append(f"- **测试步骤**:")
                    for step in case["steps"]:
                        s_num = step.get("step", "?")
                        s_action = step.get("action", "")
                        s_expected = step.get("expected", "")
                        lines.append(f"  {s_num}. {s_action} → 预期: {s_expected}")

                # 错误信息
                lines.append(f"- **错误信息**:")
                lines.append(f"  ```")
                lines.append(f"  {error_msg}")
                lines.append(f"  ```")
                lines.append("")

        # ── 4. 评分与评审 ──
        if data.review:
            lines.append("---")
            lines.append("")
            lines.append("## 4. 用例评审")
            lines.append("")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            passed_review = "通过" if data.review.get("passed") else "不通过"
            lines.append(f"| 评审结果 | {passed_review} |")
            lines.append(f"| 评分 | {data.review.get('score', 'N/A')} |")
            lines.append(f"| 未通过用例数 | {data.review.get('failed_cases', 0)} |")
            feedback = data.review.get("feedback", "")
            if feedback:
                lines.append(f"| 反馈 | {feedback} |")
            gaps = data.review.get("coverage_gaps", [])
            if gaps:
                lines.append(f"| 覆盖度缺口 | {', '.join(gaps)} |")
            lines.append("")

        # ── 5. 附录：所有用例执行明细 ──
        lines.append("---")
        lines.append("")
        lines.append("## 5. 所有用例执行明细")
        lines.append("")
        lines.append(f"| 序号 | 用例ID | 标题 | 状态 |")
        lines.append(f"|------|--------|------|------|")
        for i, detail in enumerate(data.details, 1):
            cid = detail.get("case_id", "N/A")
            title = detail.get("title", "N/A")
            status = detail.get("status", "N/A")
            lines.append(f"| {i} | `{cid}` | {title} | {status} |")
        lines.append("")

        # ── 页脚 ──
        lines.append("---")
        lines.append("")
        lines.append(f"*本报告由 iTest-Agent 自动生成于 {now_str}*")
        lines.append("")

        return "\n".join(lines)


# =============================================================================
# PDF 报告生成器
# =============================================================================


class PDFReportBuilder:
    """基于 Markdown → HTML → PDF 的 PDF 报告生成器"""

    # PDF 专用 CSS 样式（weasyprint 渲染用）
    PDF_CSS = """
    @page {
        size: A4;
        margin: 2.5cm 2cm;
        @top-center {
            content: "iTest-Agent 测试报告";
            font-size: 10pt;
            color: #666;
        }
        @bottom-center {
            content: "第 " counter(page) " 页";
            font-size: 9pt;
            color: #999;
        }
    }
    body {
        font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        font-size: 11pt;
        line-height: 1.7;
        color: #333;
    }
    h1 {
        font-size: 22pt;
        color: #2F5496;
        border-bottom: 3px solid #2F5496;
        padding-bottom: 8px;
        margin-top: 0;
    }
    h2 {
        font-size: 16pt;
        color: #2F5496;
        border-bottom: 1.5px solid #ddd;
        padding-bottom: 4px;
        margin-top: 28px;
    }
    h3 {
        font-size: 13pt;
        color: #444;
        margin-top: 20px;
    }
    table {
        border-collapse: collapse;
        width: 100%;
        margin: 12px 0;
        font-size: 10pt;
    }
    th {
        background-color: #2F5496;
        color: white;
        padding: 8px 10px;
        text-align: left;
        font-weight: bold;
    }
    td {
        padding: 6px 10px;
        border: 1px solid #ddd;
    }
    tr:nth-child(even) {
        background-color: #f7f8fb;
    }
    code {
        background-color: #f0f0f0;
        padding: 2px 5px;
        border-radius: 3px;
        font-family: "SF Mono", "Monaco", "Menlo", monospace;
        font-size: 9.5pt;
    }
    pre {
        background-color: #f5f5f5;
        padding: 12px;
        border-radius: 6px;
        border: 1px solid #e0e0e0;
        overflow-x: auto;
        font-size: 9pt;
    }
    pre code {
        background: none;
        padding: 0;
    }
    hr {
        border: none;
        border-top: 1px solid #ddd;
        margin: 24px 0;
    }
    blockquote {
        border-left: 4px solid #2F5496;
        margin: 12px 0;
        padding: 8px 16px;
        background: #f0f4fb;
        color: #555;
    }
    .progress-bar {
        background: #e0e0e0;
        padding: 4px 10px;
        border-radius: 4px;
        font-family: monospace;
    }
    """

    @staticmethod
    def convert_md_to_html(md_content: str) -> str:
        """将 Markdown 转换为 HTML"""
        import markdown as md_lib

        extensions = [
            "tables",
            "fenced_code",
            "codehilite",
            "toc",
            "nl2br",
        ]
        html_body = md_lib.markdown(md_content, extensions=extensions)

        # 包装为完整 HTML 文档
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>iTest-Agent 测试报告</title>
<style>
{PDFReportBuilder.PDF_CSS}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
        return full_html

    @staticmethod
    def generate(md_content: str, pdf_path: str) -> str:
        """
        从 Markdown 内容生成 PDF 文件

        Args:
            md_content: Markdown 格式的报告内容
            pdf_path: PDF 输出文件路径

        Returns:
            输出文件的绝对路径
        """
        from weasyprint import HTML

        # 确保输出目录存在
        pdf_dir = os.path.dirname(pdf_path)
        if pdf_dir:
            os.makedirs(pdf_dir, exist_ok=True)

        html_content = PDFReportBuilder.convert_md_to_html(md_content)
        HTML(string=html_content).write_pdf(pdf_path)
        return os.path.abspath(pdf_path)


# =============================================================================
# 报告生成器主类
# =============================================================================


class ReportGenerator:
    """
    测试报告生成器

    核心流程：
    1. 读取 execution_log.json（必需）
    2. 读取 test_suite.json（可选，提供用例详情）
    3. 读取 analysis_result.json（可选，提供产品/模块信息）
    4. 读取 review_result.json（可选，提供评审信息）
    5. 生成 Markdown 报告
    6. 生成 PDF 报告（基于 Markdown）

    Usage:
        gen = ReportGenerator()
        paths = gen.generate(
            execution_log_path="output/execution_log.json",
            test_suite_path="output/test_suite.json",
            output_dir="output",
        )
        print(paths["markdown"], paths["pdf"])
    """

    def __init__(self, pdf_enabled: bool = True):
        """
        Args:
            pdf_enabled: 是否启用 PDF 导出（需要 weasyprint）
        """
        self.pdf_enabled = pdf_enabled

    def _load_json(self, path: str) -> Dict[str, Any]:
        """安全加载 JSON 文件"""
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def generate(
        self,
        execution_log_path: str,
        test_suite_path: str = "",
        analysis_result_path: str = "",
        review_result_path: str = "",
        output_dir: str = "",
    ) -> Dict[str, str]:
        """
        生成测试报告（Markdown + PDF）

        Args:
            execution_log_path: 执行日志 JSON 路径（必需）
            test_suite_path: 测试用例集 JSON 路径
            analysis_result_path: 需求分析结果 JSON 路径
            review_result_path: 评审结果 JSON 路径
            output_dir: 输出目录（默认与 execution_log 同目录）

        Returns:
            {"markdown": md_path, "pdf": pdf_path}
            如果 PDF 生成失败，pdf 键仍存在但值为空字符串

        Raises:
            FileNotFoundError: execution_log_path 不存在
            ValueError: 执行日志 JSON 缺少必要字段
        """
        # 1. 加载数据
        if not execution_log_path or not os.path.exists(execution_log_path):
            raise FileNotFoundError(f"执行日志不存在: {execution_log_path}")

        exec_log = self._load_json(execution_log_path)
        if "total" not in exec_log:
            raise ValueError("执行日志缺少 'total' 字段，请确认文件格式")

        suite = self._load_json(test_suite_path) if test_suite_path else {}
        analysis = self._load_json(analysis_result_path) if analysis_result_path else {}
        review = self._load_json(review_result_path) if review_result_path else {}

        # 2. 构建报告数据
        data = ReportData(
            execution_log=exec_log,
            test_suite=suite,
            analysis_result=analysis,
            review_result=review,
        )

        # 3. 生成 Markdown
        if not output_dir:
            output_dir = os.path.dirname(execution_log_path)
        os.makedirs(output_dir, exist_ok=True)

        md_content = MarkdownReportBuilder.build(data)
        md_path = os.path.join(output_dir, "test_report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 4. 生成 PDF
        pdf_path = ""
        if self.pdf_enabled:
            try:
                pdf_path = os.path.join(output_dir, "test_report.pdf")
                PDFReportBuilder.generate(md_content, pdf_path)
                print(f"[报告] PDF 已生成: {pdf_path}")
            except Exception as e:
                pdf_path = ""
                print(f"[警告] PDF 生成失败: {e}")

        print(f"[报告] Markdown 已生成: {md_path}")
        return {"markdown": md_path, "pdf": pdf_path}

    def generate_markdown_only(
        self,
        execution_log_path: str,
        test_suite_path: str = "",
        analysis_result_path: str = "",
        review_result_path: str = "",
        output_dir: str = "",
    ) -> str:
        """仅生成 Markdown 报告，返回文件路径"""
        result = self.generate(
            execution_log_path=execution_log_path,
            test_suite_path=test_suite_path,
            analysis_result_path=analysis_result_path,
            review_result_path=review_result_path,
            output_dir=output_dir,
        )
        return result["markdown"]


# =============================================================================
# 独立运行入口：python -m agents.report_generator
# =============================================================================


def main() -> None:
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="iTest-Agent 报告生成器 — 从执行日志生成 Markdown/PDF 测试报告"
    )
    parser.add_argument(
        "execution_log",
        help="执行日志 JSON 文件路径（execution_log.json）",
    )
    parser.add_argument(
        "--suite",
        default="",
        help="测试用例集 JSON 路径（test_suite.json，可选）",
    )
    parser.add_argument(
        "--analysis",
        default="",
        help="需求分析结果 JSON 路径（可选）",
    )
    parser.add_argument(
        "--review",
        default="",
        help="评审结果 JSON 路径（可选）",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="",
        help="输出目录（默认与执行日志同目录）",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="跳过 PDF 生成（仅输出 Markdown）",
    )
    args = parser.parse_args()

    gen = ReportGenerator(pdf_enabled=not args.no_pdf)

    try:
        paths = gen.generate(
            execution_log_path=args.execution_log,
            test_suite_path=args.suite,
            analysis_result_path=args.analysis,
            review_result_path=args.review,
            output_dir=args.output_dir,
        )
        print(f"\n报告生成完成!")
        print(f"  Markdown: {paths['markdown']}")
        if paths.get("pdf"):
            print(f"  PDF:      {paths['pdf']}")
    except Exception as e:
        print(f"\n[错误] 报告生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
