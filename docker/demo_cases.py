"""
iTest-Agent Demo 用例集（阶段三交付物）

针对 docker/demo_app.py 的真实执行用例集。test_data 携带 selectors
与 URL，使 step_parser 能把自然语言步骤映射为真实浏览器操作。

两个缺陷用例（验证缺陷聚类）：
- TC-DEMO-002：密码过短注册 → BUG-001 文案错误（预期 8-20，实际提示 6-20）
- TC-DEMO-005：错误密码登录 → BUG-002 无失败提示

启动顺序：
1. python docker/demo_app.py                 （被测系统 :8090）
2. python docker/run_demo_cases.py           （MCP 真实执行）
"""

import json
import os
import sys

# 保证可独立运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = "http://127.0.0.1:8090"

# 中文 key 与动作文本匹配（step_parser 的 _match_selector 用 key 匹配）
SELECTORS = {
    "手机号": "#reg-phone",
    "密码": "#reg-password",
    "昵称": "#reg-nickname",
    "注册": "#reg-submit",
    "提示": "#reg-msg",
}

# 登录页独立选择器（避免与注册页字段混淆）
LOGIN_SELECTORS = {
    "登录": "#tab-login",
    "登录手机号": "#login-phone",
    "登录密码": "#login-password",
    "登录按钮": "#login-submit",
    "登录提示": "#login-msg",
}

CASES = [
    {
        "case_id": "TC-DEMO-001",
        "title": "正常注册 - Happy Path",
        "function_id": "FUNC-001",
        "function_name": "用户注册",
        "type": "功能测试",
        "priority": "P0",
        "precondition": "无",
        "test_data": {
            "url": BASE_URL,
            "selectors": SELECTORS,
            "values": {"手机号": "13800138000", "密码": "Test@1234", "昵称": "测试用户"},
        },
        "steps": [
            {"step": 1, "action": "打开页面 http://127.0.0.1:8090", "expected": "页面加载成功"},
            {"step": 2, "action": "输入手机号 13800138000", "expected": "手机号已填写"},
            {"step": 3, "action": "输入密码 Test@1234", "expected": "密码已填写"},
            {"step": 4, "action": "输入昵称 测试用户", "expected": "昵称已填写"},
            {"step": 5, "action": "点击注册按钮", "expected": "提交注册"},
            {"step": 6, "action": "验证提示 注册成功", "expected": "注册成功"},
        ],
    },
    {
        "case_id": "TC-DEMO-002",
        "title": "密码过短注册 - 边界校验",
        "function_id": "FUNC-001",
        "function_name": "用户注册",
        "type": "功能测试",
        "priority": "P1",
        "precondition": "无",
        "test_data": {
            "url": BASE_URL,
            "selectors": SELECTORS,
            "values": {"手机号": "13800138001", "密码": "Test1", "昵称": "测试用户2"},
        },
        "steps": [
            {"step": 1, "action": "打开页面 http://127.0.0.1:8090", "expected": "页面加载成功"},
            {"step": 2, "action": "输入手机号 13800138001", "expected": "手机号已填写"},
            {"step": 3, "action": "输入密码 Test1", "expected": "密码已填写"},
            {"step": 4, "action": "输入昵称 测试用户2", "expected": "昵称已填写"},
            {"step": 5, "action": "点击注册按钮", "expected": "提交注册"},
            {"step": 6, "action": "验证提示 密码长度需为 8-20 位", "expected": "密码长度需为 8-20 位"},
        ],
    },
    {
        "case_id": "TC-DEMO-003",
        "title": "手机号已注册 - 异常流程",
        "function_id": "FUNC-001",
        "function_name": "用户注册",
        "type": "功能测试",
        "priority": "P1",
        "precondition": "TC-DEMO-001 已注册 13800138000",
        "test_data": {
            "url": BASE_URL,
            "selectors": SELECTORS,
            "values": {"手机号": "13800138000", "密码": "Test@1234", "昵称": "重复用户"},
        },
        "steps": [
            {"step": 1, "action": "打开页面 http://127.0.0.1:8090", "expected": "页面加载成功"},
            {"step": 2, "action": "输入手机号 13800138000", "expected": "手机号已填写"},
            {"step": 3, "action": "输入密码 Test@1234", "expected": "密码已填写"},
            {"step": 4, "action": "输入昵称 重复用户", "expected": "昵称已填写"},
            {"step": 5, "action": "点击注册按钮", "expected": "提交注册"},
            {"step": 6, "action": "验证提示 该手机号已注册", "expected": "该手机号已注册"},
        ],
    },
    {
        "case_id": "TC-DEMO-004",
        "title": "正确账号登录 - Happy Path",
        "function_id": "FUNC-002",
        "function_name": "用户登录",
        "type": "功能测试",
        "priority": "P0",
        "precondition": "13800138000 已注册",
        "test_data": {
            "url": BASE_URL,
            "selectors": LOGIN_SELECTORS,
            "values": {"登录手机号": "13800138000", "登录密码": "Test@1234"},
        },
        "steps": [
            {"step": 1, "action": "打开页面 http://127.0.0.1:8090", "expected": "页面加载成功"},
            {"step": 2, "action": "点击 登录", "expected": "切换到登录页"},
            {"step": 3, "action": "输入登录手机号 13800138000", "expected": "手机号已填写"},
            {"step": 4, "action": "输入登录密码 Test@1234", "expected": "密码已填写"},
            {"step": 5, "action": "点击 登录按钮", "expected": "提交登录"},
            {"step": 6, "action": "验证登录提示 登录成功", "expected": "登录成功"},
        ],
    },
    {
        "case_id": "TC-DEMO-005",
        "title": "错误密码登录 - 失败提示",
        "function_id": "FUNC-002",
        "function_name": "用户登录",
        "type": "功能测试",
        "priority": "P1",
        "precondition": "13800138000 已注册",
        "test_data": {
            "url": BASE_URL,
            "selectors": LOGIN_SELECTORS,
            "values": {"登录手机号": "13800138000", "登录密码": "Wrong@9999"},
        },
        "steps": [
            {"step": 1, "action": "打开页面 http://127.0.0.1:8090", "expected": "页面加载成功"},
            {"step": 2, "action": "点击 登录", "expected": "切换到登录页"},
            {"step": 3, "action": "输入登录手机号 13800138000", "expected": "手机号已填写"},
            {"step": 4, "action": "输入登录密码 Wrong@9999", "expected": "密码已填写"},
            {"step": 5, "action": "点击 登录按钮", "expected": "提交登录"},
            {"step": 6, "action": "验证登录提示 手机号或密码错误", "expected": "手机号或密码错误"},
        ],
    },
]


def write_suite(path: str = "") -> str:
    """将用例集写为 test_suite.json（供执行引擎/报告生成使用）"""
    suite = {
        "suite_name": "iTest-Agent Demo 用例集",
        "product_name": "iTest-Agent 演示系统",
        "module_name": "用户账户模块",
        "total_cases": len(CASES),
        "test_cases": CASES,
    }
    if not path:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output", "demo_suite.json",
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)
    print(f"[OK] 用例集已写入: {path} ({len(CASES)} 条)")
    return path


if __name__ == "__main__":
    write_suite()
