"""
录制 iTest-Agent 前端完整流水线演示 GIF（v3）。

流程：打开前端 -> 上传 sample PRD -> 勾选 Mock LLM -> 开始测试
      -> 等待任务完成 -> 轮播结果 Tab（功能点/用例/评审/执行/报告）

演示产品核心：输入 PRD 自动完成「需求分析→用例生成→评审→执行→报告」。
"""
import os
import time

from playwright.sync_api import sync_playwright

FRONT = "http://127.0.0.1:7860"
PRD = "/Users/mac/Documents/面试与求职/求职计划与日志/iTest-Agent/tests/sample_prd.md"
VIDEO_DIR = "/tmp/itest_gif"
os.makedirs(VIDEO_DIR, exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=VIDEO_DIR,
            viewport={"width": 1280, "height": 860},
            device_scale_factor=2,
        )
        page = context.new_page()
        page.goto(FRONT, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # ① 标题页
        page.screenshot(path=f"{VIDEO_DIR}/d0_title.png")

        # ② 上传 PRD（设置文件输入）
        file_input = page.locator('input[type="file"]').first
        if file_input.count():
            file_input.set_input_files(PRD)
            page.wait_for_timeout(1500)

        # ③ 勾选 Mock LLM
        mock_cb = page.locator('label:has-text("Mock LLM") input[type="checkbox"]')
        if mock_cb.count():
            # 确保勾选
            if not mock_cb.first.is_checked():
                mock_cb.first.click()
                page.wait_for_timeout(500)

        page.screenshot(path=f"{VIDEO_DIR}/d1_prd_uploaded.png")

        # ④ 开始测试
        start_btn = page.locator('button:has-text("开始测试")').first
        start_btn.click()

        # ⑤ 等待任务完成（轮询状态文本变为「已完成」）
        page.wait_for_timeout(3000)
        page.screenshot(path=f"{VIDEO_DIR}/d2_running.png")

        # 等待进度条完成（最多 120s）
        deadline = time.time() + 120
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            status = page.locator('[role=tab]:has-text("执行结果")').first
            # 检测「自动刷新」停止 / 状态区变化
            txt = page.locator(".gradio-container").first.text_content() or ""
            if "已完成" in txt or "完成" in txt:
                break

        page.wait_for_timeout(2000)
        # ⑥ 轮播结果 Tab
        tabs = ["功能点", "测试用例", "评审结果", "执行结果", "测试报告", "运行日志"]
        for i, name in enumerate(tabs, start=3):
            tab = page.locator(f'[role=tab]:has-text("{name}")').first
            if tab.count():
                tab.click()
                page.wait_for_timeout(2500)
                page.screenshot(path=f"{VIDEO_DIR}/d{i}_{name}.png")

        context.close()
        browser.close()
        print("录制完成")

    webm = [os.path.join(VIDEO_DIR, f) for f in os.listdir(VIDEO_DIR) if f.endswith(".webm")]
    print(f"视频: {webm}")


if __name__ == "__main__":
    main()
