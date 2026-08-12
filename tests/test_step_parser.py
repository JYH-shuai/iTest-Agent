"""
执行引擎步骤解析器单元测试
"""

import pytest

from execution.step_parser import UIAction, parse_case


class TestParseAPICase:
    """接口用例解析"""

    def test_structured_fields(self):
        case = {
            "case_id": "TC-API-001",
            "title": "登录接口",
            "type": "接口测试",
            "test_data": {
                "method": "POST",
                "url": "https://api.example.com/login",
                "json": {"username": "admin"},
                "expected_status": 200,
                "expected_json_field": "code",
                "expected_json_value": 0,
            },
            "steps": [],
        }
        plan = parse_case(case)
        assert plan.kind == "api"
        assert plan.method == "POST"
        assert plan.url == "https://api.example.com/login"
        assert plan.expected_status == 200
        assert plan.expected_json_field == "code"
        assert plan.expected_json_value == 0

    def test_extract_url_from_step(self):
        case = {
            "case_id": "TC-API-002",
            "title": "查询接口",
            "type": "接口测试",
            "test_data": {},
            "steps": [
                {
                    "step": 1,
                    "action": "GET http://127.0.0.1:8000/api/items",
                    "expected": "返回 200",
                }
            ],
        }
        plan = parse_case(case)
        assert plan.kind == "api"
        assert plan.url == "http://127.0.0.1:8000/api/items"
        assert plan.method == "GET"
        assert plan.expected_status == 200

    def test_missing_url_falls_back_to_simulated(self):
        case = {
            "case_id": "TC-API-003",
            "title": "无 URL 接口",
            "type": "接口测试",
            "test_data": {},
            "steps": [{"step": 1, "action": "发送请求", "expected": "成功"}],
        }
        plan = parse_case(case)
        assert plan.kind == "simulated"


class TestParseUICase:
    """UI 用例解析"""

    def test_ui_action_mapping(self):
        case = {
            "case_id": "TC-UI-001",
            "title": "登录流程",
            "type": "功能测试",
            "test_data": {
                "url": "http://example.com/login",
                "selectors": {
                    "用户名": "#username",
                    "密码": "#password",
                    "登录": "#login-btn",
                    "错误提示": ".error",
                },
            },
            "steps": [
                {"step": 1, "action": "打开 http://example.com/login", "expected": "加载"},
                {"step": 2, "action": "输入 admin 到 用户名", "expected": ""},
                {"step": 3, "action": "输入 123456 到 密码", "expected": ""},
                {"step": 4, "action": "点击 登录", "expected": ""},
                {
                    "step": 5,
                    "action": "验证 错误提示 显示 用户名或密码错误",
                    "expected": "用户名或密码错误",
                },
            ],
        }
        plan = parse_case(case)
        assert plan.kind == "ui"
        actions = plan.ui_actions
        assert actions[0].action == "navigate"
        # 重复导航被去重
        assert sum(1 for a in actions if a.action == "navigate") == 1
        # 选择器按最早出现的关键词匹配
        fill_username = [a for a in actions if a.action == "fill"][0]
        assert fill_username.selector == "#username"
        assert fill_username.value == "admin"
        assert_actions = [a for a in actions if a.action == "assert_text"]
        assert assert_actions[0].selector == ".error"
        assert assert_actions[0].expected_text == "用户名或密码错误"

    def test_unmappable_step_becomes_simulate(self):
        case = {
            "case_id": "TC-UI-002",
            "title": "未知操作",
            "type": "功能测试",
            "test_data": {},
            "steps": [{"step": 1, "action": "执行自定义操作", "expected": ""}],
        }
        plan = parse_case(case)
        assert plan.ui_actions[-1].action == "simulate"

    def test_no_actions_falls_back_to_simulated(self):
        case = {
            "case_id": "TC-UI-003",
            "title": "空用例",
            "type": "功能测试",
            "test_data": {},
            "steps": [],
        }
        plan = parse_case(case)
        assert plan.kind == "simulated"
