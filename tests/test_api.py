"""
FastAPI 服务层集成测试
"""

import io
import os

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["ITEST_MOCK_LLM"] = "1"
os.environ["ITEST_EXECUTION_MODE"] = "simulated"
os.environ["ITEST_OUTPUT_ROOT"] = "/tmp/itest_api_test"

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module")
def prd_file():
    path = os.path.join(_PROJECT_ROOT, "tests", "sample_prd.md")
    with open(path, "rb") as f:
        return f.read()


class TestHealth:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestPipeline:
    def test_sync_pipeline(self, prd_file):
        resp = client.post(
            "/api/v1/pipeline",
            files={"file": ("sample_prd.md", io.BytesIO(prd_file), "text/markdown")},
            data={
                "mock_llm": "true",
                "execution_mode": "simulated",
                "sync": "true",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        task_id = body["task_id"]
        assert body["status"] == "completed"

        # 查询任务状态
        status = client.get(f"/api/v1/tasks/{task_id}")
        assert status.status_code == 200
        data = status.json()
        assert data["status"] == "completed"
        assert data["analysis"]["total_functions"] >= 5
        assert data["test_suite"]["total_cases"] > 0
        assert data["review"]["passed"] is True
        assert data["execution"]["total"] > 0
        assert all(isinstance(m, str) for m in data["messages"])

        # 下载 Markdown 报告
        md = client.get(f"/api/v1/tasks/{task_id}/report?format=md")
        assert md.status_code == 200
        assert "iTest-Agent 测试报告" in md.text

        # 下载 JSON 执行日志
        jlog = client.get(f"/api/v1/tasks/{task_id}/report?format=json")
        assert jlog.status_code == 200
        assert "total" in jlog.json()

    def test_invalid_format_rejected(self):
        resp = client.post(
            "/api/v1/pipeline",
            files={"file": ("prd.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 400

    def test_target_url_in_options(self, prd_file):
        """入参 target_url 应落入任务 options（供执行节点读取），与执行模式解耦"""
        resp = client.post(
            "/api/v1/pipeline",
            files={"file": ("sample_prd.md", io.BytesIO(prd_file), "text/markdown")},
            data={
                "mock_llm": "true",
                "execution_mode": "simulated",
                "sync": "true",
                "target_url": "http://127.0.0.1:8090",
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]
        # 直接查任务存储的 options，确认 target_url 被持久化
        from api.main import TASK_STORE
        task = TASK_STORE.get_task(task_id)
        assert task is not None
        assert task["options"]["target_url"] == "http://127.0.0.1:8090"


class TestTaskNotFound:
    def test_missing_task(self):
        resp = client.get("/api/v1/tasks/not_exist")
        assert resp.status_code == 404
