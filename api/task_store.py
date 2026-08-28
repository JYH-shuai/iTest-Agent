"""
任务管理：内存存储 + 磁盘持久化（JSON）

支持：
- 创建任务（含输出目录、Checkpoint 路径）
- 更新任务状态与阶段产物
- 按 task_id 查询
- 服务重启后从磁盘恢复任务列表
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TaskStore:
    """线程安全的任务存储"""

    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.tasks_file = os.path.join(root_dir, "tasks.json")
        os.makedirs(root_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        """从磁盘恢复任务列表"""
        if not os.path.exists(self.tasks_file):
            return
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._tasks = data
        except Exception:
            self._tasks = {}

    def _save(self) -> None:
        """持久化任务列表到磁盘"""
        tmp = self.tasks_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, self.tasks_file)

    # ---- CRUD ----

    def create_task(
        self,
        prd_filename: str,
        prd_path: str,
        output_dir: str,
        options: Optional[Dict[str, Any]] = None,
        incremental: bool = False,
        base_task_id: str = "",
    ) -> str:
        """创建任务并返回 task_id"""
        task_id = f"task_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "phase": "init",
                "created_at": now,
                "updated_at": now,
                "prd_filename": prd_filename,
                "prd_path": prd_path,
                "output_dir": output_dir,
                "options": options or {},
                "incremental": incremental,
                "base_task_id": base_task_id,
                "analysis": {},
                "test_suite": {},
                "review": {},
                "execution": {},
                "report_path": "",
                "traceability_path": "",
                "messages": [],
                "error": "",
            }
            self._save()
        return task_id

    def update_task(self, task_id: str, **fields: Any) -> None:
        """更新任务字段（线程安全）；phase 变更时记录阶段耗时"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"任务不存在: {task_id}")
            # 阶段耗时统计：phase 变化时闭合上一阶段并开启新阶段
            new_phase = fields.get("phase")
            if new_phase and new_phase != task.get("phase"):
                times = task.setdefault("phase_times", {})
                prev_start = times.pop("_start_ts", None)
                prev_phase = task.get("phase")
                if prev_start and prev_phase:
                    prev_phase = prev_phase.replace(" ", "_")
                    times[prev_phase] = round(time.time() - prev_start, 1)
                times["_start_ts"] = time.time()
            task.update(fields)
            task["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            self._save()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按 task_id 查询任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list_tasks(self, limit: int = 50) -> list:
        """列出最近任务（按创建时间倒序）"""
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.get("created_at", ""),
                reverse=True,
            )
            return [dict(t) for t in tasks[:limit]]

    def append_message(self, task_id: str, message: str) -> None:
        """追加任务消息"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.setdefault("messages", []).append(
                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}"
            )
            task["updated_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            self._save()

    @staticmethod
    def wait_for_completion(
        task_store: "TaskStore",
        task_id: str,
        timeout: float = 600.0,
        poll_interval: float = 1.0,
    ) -> Dict[str, Any]:
        """同步等待任务完成（用于 sync=true 调用）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = task_store.get_task(task_id)
            if task and task.get("status") in ("completed", "failed", "cancelled"):
                return task
            time.sleep(poll_interval)
        task = task_store.get_task(task_id) or {}
        task["error"] = task.get("error") or "等待超时"
        return task
