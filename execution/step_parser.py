"""
测试步骤解析器

将 TestCase（自然语言步骤 + test_data）解析为可执行计划：

- API 用例（接口测试）：提取 method / url / 请求参数 / 期望断言
- UI 用例（功能/兼容性/安全等）：将动作文本映射为浏览器操作序列

解析策略（启发式，保证可解释）：
1. 优先使用 test_data 中的结构化字段（method/url/expected_status 等）；
2. 其次从步骤文本中提取 URL 与 HTTP 方法关键词；
3. UI 动作关键词 → 操作类型 + 选择器（选择器优先取 test_data["selectors"]）。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# =============================================================================
# 可执行计划数据结构
# =============================================================================


@dataclass
class UIAction:
    """UI 单步操作"""

    action: str  # navigate / click / fill / assert_text / wait / screenshot
    selector: str = ""
    value: str = ""
    expected_text: str = ""
    timeout_ms: int = 10000


@dataclass
class CasePlan:
    """解析后的可执行用例计划"""

    case_id: str = ""
    title: str = ""
    kind: str = "simulated"  # api / ui / simulated
    # ---- API 字段 ----
    method: str = "GET"
    url: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    json_body: Dict[str, Any] = field(default_factory=dict)
    expected_status: Optional[int] = None
    expected_json_field: Optional[str] = None
    expected_json_value: Any = None
    # ---- UI 字段 ----
    ui_actions: List[UIAction] = field(default_factory=list)
    # ---- 原始信息 ----
    source_steps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# =============================================================================
# 关键词规则
# =============================================================================

_NAVIGATE_KEYWORDS = ("打开", "访问", "进入", "跳转", "导航", "加载")
_CLICK_KEYWORDS = ("点击", "选择", "提交", "登录", "按下", "选中", "切换", "关闭弹窗")
_FILL_KEYWORDS = ("输入", "填写", "键入", "录入", "填入")
_ASSERT_KEYWORDS = ("验证", "检查", "断言", "确认", "应显示", "出现", "提示", "展示", "期望")
_WAIT_KEYWORDS = ("等待", "加载完成")
_SCREENSHOT_KEYWORDS = ("截图", "拍照")

_METHOD_KEYWORDS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
}

_URL_PATTERN = re.compile(r"https?://[^\s，。；、）)]+", re.IGNORECASE)


# =============================================================================
# 解析器
# =============================================================================


def parse_case(test_case: Dict[str, Any]) -> CasePlan:
    """
    将 TestCase 字典解析为 CasePlan。

    Args:
        test_case: TestCase.model_dump() 或等价的字典

    Returns:
        CasePlan: 可执行计划
    """
    case_id = test_case.get("case_id", "")
    title = test_case.get("title", "")
    case_type = test_case.get("type", "功能测试")
    test_data = test_case.get("test_data", {}) or {}
    steps = test_case.get("steps", []) or []
    source_steps = [
        f"{s.get('step', i + 1)}. {s.get('action', '')} -> {s.get('expected', '')}"
        for i, s in enumerate(steps)
    ]

    # ---- 接口测试：优先结构化字段 ----
    if case_type == "接口测试" or "api" in str(test_data).lower():
        plan = _parse_api_case(case_id, title, test_data, steps)
        plan.source_steps = source_steps
        return plan

    # ---- UI 测试 ----
    plan = _parse_ui_case(case_id, title, test_data, steps)
    plan.source_steps = source_steps
    return plan


def _parse_api_case(
    case_id: str,
    title: str,
    test_data: Dict[str, Any],
    steps: List[Dict[str, Any]],
) -> CasePlan:
    plan = CasePlan(case_id=case_id, title=title, kind="api")

    # 1) test_data 结构化字段
    plan.method = str(test_data.get("method", "GET")).upper()
    plan.url = str(test_data.get("url", "") or test_data.get("path", ""))
    plan.params = test_data.get("params", {}) or {}
    plan.headers = test_data.get("headers", {}) or {}
    plan.json_body = test_data.get("json", {}) or test_data.get("json_body", {}) or {}
    plan.expected_status = test_data.get("expected_status")
    plan.expected_json_field = test_data.get("expected_json_field")
    plan.expected_json_value = test_data.get("expected_json_value")

    # 2) 步骤文本补充
    for step in steps:
        action_text = str(step.get("action", ""))
        expected_text = str(step.get("expected", ""))

        if not plan.url:
            m = _URL_PATTERN.search(action_text)
            if m:
                plan.url = m.group(0)

        if plan.method == "GET":
            for kw, method in _METHOD_KEYWORDS.items():
                if kw in action_text.lower() or kw in expected_text.lower():
                    plan.method = method
                    break

        if plan.expected_status is None:
            m = re.search(r"(?:返回|状态码|status)[^\d]*(\d{3})", expected_text)
            if m:
                plan.expected_status = int(m.group(1))

        if plan.expected_json_field is None and "json" in test_data:
            plan.expected_json_field = test_data["json"].get("expected_field")
            plan.expected_json_value = test_data["json"].get("expected_value")

    if not plan.url:
        plan.notes.append("未解析出 URL，将按模拟执行处理")
        plan.kind = "simulated"

    return plan


def _parse_ui_case(
    case_id: str,
    title: str,
    test_data: Dict[str, Any],
    steps: List[Dict[str, Any]],
) -> CasePlan:
    plan = CasePlan(case_id=case_id, title=title, kind="ui")
    selectors = test_data.get("selectors", {}) or {}
    start_url = str(test_data.get("url", "") or "")

    # 起始导航
    if start_url:
        plan.ui_actions.append(UIAction(action="navigate", value=start_url))

    for step in steps:
        action_text = str(step.get("action", ""))
        expected_text = str(step.get("expected", ""))

        # URL 导航
        m = _URL_PATTERN.search(action_text)
        if m and ("打开" in action_text or "访问" in action_text or "进入" in action_text):
            # 与起始导航重复时跳过
            if any(
                a.action == "navigate" and a.value == m.group(0)
                for a in plan.ui_actions
            ):
                continue
            plan.ui_actions.append(UIAction(action="navigate", value=m.group(0)))
            continue

        # 填充动作：尝试从 test_data 获取选择器
        if any(kw in action_text for kw in _FILL_KEYWORDS):
            selector = _match_selector(action_text, selectors)
            value = _extract_fill_value(action_text)
            if selector is None:
                plan.notes.append(f"步骤「{action_text}」缺少选择器，按模拟处理")
                plan.ui_actions.append(
                    UIAction(action="simulate", value=action_text)
                )
                continue
            plan.ui_actions.append(
                UIAction(action="fill", selector=selector, value=value)
            )
            continue

        # 点击动作
        if any(kw in action_text for kw in _CLICK_KEYWORDS):
            selector = _match_selector(action_text, selectors)
            if selector is None:
                plan.notes.append(f"步骤「{action_text}」缺少选择器，按模拟处理")
                plan.ui_actions.append(UIAction(action="simulate", value=action_text))
                continue
            plan.ui_actions.append(UIAction(action="click", selector=selector))
            continue

        # 断言动作
        if any(kw in action_text for kw in _ASSERT_KEYWORDS) or expected_text:
            selector = _match_selector(action_text, selectors)
            expected = expected_text or action_text
            if selector is None:
                plan.notes.append(f"断言步骤「{action_text}」缺少选择器，仅记录预期")
                plan.ui_actions.append(
                    UIAction(action="assert_text", expected_text=expected)
                )
                continue
            plan.ui_actions.append(
                UIAction(
                    action="assert_text",
                    selector=selector,
                    expected_text=expected,
                )
            )
            continue

        # 等待动作
        if any(kw in action_text for kw in _WAIT_KEYWORDS):
            selector = _match_selector(action_text, selectors)
            plan.ui_actions.append(
                UIAction(
                    action="wait",
                    selector=selector or "body",
                    expected_text=expected_text,
                )
            )
            continue

        # 截图动作
        if any(kw in action_text for kw in _SCREENSHOT_KEYWORDS):
            plan.ui_actions.append(UIAction(action="screenshot"))
            continue

        # 无法识别 → 模拟执行
        plan.notes.append(f"步骤「{action_text}」无法映射到浏览器操作，按模拟处理")
        plan.ui_actions.append(UIAction(action="simulate", value=action_text))

    if not plan.ui_actions:
        plan.notes.append("无可用 UI 操作，按模拟执行处理")
        plan.kind = "simulated"

    return plan


def _match_selector(action_text: str, selectors: Dict[str, str]) -> Optional[str]:
    """
    根据动作文本关键词匹配 test_data 中的选择器。

    匹配策略：选择在文本中最早出现的关键词（避免 "用户名" 与
    "错误提示" 同时出现时误匹配），其次匹配最长关键词。
    """
    candidates = []
    for key, selector in selectors.items():
        if key and key in action_text:
            pos = action_text.index(key)
            candidates.append((pos, -len(key), selector))
    if candidates:
        candidates.sort()
        return candidates[0][2]
    # 常见选择器兜底
    if "登录" in action_text or "提交" in action_text:
        return selectors.get("submit") or selectors.get("login") or "button[type=submit]"
    if "用户名" in action_text or "账号" in action_text or "邮箱" in action_text:
        return selectors.get("username") or selectors.get("account") or "input[name=username]"
    if "密码" in action_text:
        return selectors.get("password") or "input[name=password]"
    if "验证码" in action_text:
        return selectors.get("captcha") or "input[name=captcha]"
    return None


def _extract_fill_value(action_text: str) -> str:
    """从填充动作文本中提取要填写的值"""
    # 支持 "输入 xxx 到 yyy" / "填写 yyy" / "输入：xxx"
    m = re.search(r"(?:输入|填写|键入|填入)[：:为 ]?[\"']?([^\"'，。；、]+)", action_text)
    if m:
        value = m.group(1).strip()
        # 去掉尾部动词短语，如 "点击登录"
        value = re.sub(r"(?:到|至|然后|并).*$", "", value).strip()
        if value:
            return value
    return ""
