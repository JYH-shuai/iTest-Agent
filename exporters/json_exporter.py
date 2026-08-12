"""
JSON 导出器 — 将 TestSuite / TestCase 导出为结构化 JSON 文件

特性：
- 支持缩进美化与紧凑两种模式
- 自动创建输出目录
- 返回输出文件的绝对路径
"""

import json
import os
from typing import List, Optional

from models.test_case import TestCase, TestSuite


class JsonExporter:
    """
    JSON 格式导出器

    Usage:
        exporter = JsonExporter()
        path = exporter.export_suite(suite, "/output/test_cases.json")
        path = exporter.export_cases(suite.test_cases, "/output/cases.json")
    """

    def __init__(self, indent: int = 2, ensure_ascii: bool = False):
        """
        Args:
            indent: JSON 缩进空格数（None = 紧凑模式，无换行无空格）
            ensure_ascii: 是否转义非 ASCII 字符
        """
        self.indent = indent
        self.ensure_ascii = ensure_ascii

    def _ensure_dir(self, file_path: str) -> None:
        """确保输出目录存在"""
        parent = os.path.dirname(file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _serialize(self, obj) -> str:
        """将 Pydantic 模型序列化为 JSON 字符串"""
        if isinstance(obj, TestSuite):
            data = obj.model_dump()
        elif isinstance(obj, list):
            data = [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in obj]
        else:
            data = obj.model_dump() if hasattr(obj, "model_dump") else obj

        # 紧凑模式：indent 为 None 或 0
        if self.indent is None or self.indent == 0:
            return json.dumps(
                data, indent=None, separators=(",", ":"), ensure_ascii=self.ensure_ascii
            )
        return json.dumps(data, indent=self.indent, ensure_ascii=self.ensure_ascii)

    def export_suite(self, suite: TestSuite, output_path: str) -> str:
        """
        导出完整 TestSuite 为 JSON 文件

        Args:
            suite: 测试套件实例
            output_path: 输出 .json 文件路径

        Returns:
            输出文件的绝对路径
        """
        self._ensure_dir(output_path)
        content = self._serialize(suite)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return os.path.abspath(output_path)

    def export_cases(self, cases: List[TestCase], output_path: str) -> str:
        """
        导出 TestCase 列表为 JSON 文件（不含套件包装）

        Args:
            cases: 用例列表
            output_path: 输出 .json 文件路径

        Returns:
            输出文件的绝对路径
        """
        self._ensure_dir(output_path)
        content = self._serialize(cases)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return os.path.abspath(output_path)

    def export_to_string(self, suite: TestSuite) -> str:
        """序列化为 JSON 字符串（不写文件）"""
        return self._serialize(suite)
