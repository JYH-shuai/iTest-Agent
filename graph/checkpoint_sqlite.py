"""
SQLite Checkpoint 持久化模块

基于官方 ``langgraph-checkpoint-sqlite`` 的 SqliteSaver 封装，
保持原有 ``SQLiteCheckpoint(db_path=...)`` 接口不变。

功能：
- 以 SQLite 数据库存储 checkpoint 快照（含 pending writes）
- 支持 thread_id 隔离（多会话并发安全）
- 支持流式执行所需的 put_writes 接口
- 支持 get / put / list 标准接口与中断恢复

Usage:
    from graph.checkpoint_sqlite import SQLiteCheckpoint
    checkpointer = SQLiteCheckpoint(db_path="./itest_checkpoints.db")
    app = workflow.compile(checkpointer=checkpointer)
"""

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


class SQLiteCheckpoint(SqliteSaver):
    """
    SQLite 持久化 Checkpoint 存储（官方 SqliteSaver 封装）

    Args:
        db_path: SQLite 数据库文件路径
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # check_same_thread=False：LangGraph 节点在线程池中执行，连接需跨线程复用
        conn = sqlite3.connect(db_path, check_same_thread=False)
        super().__init__(conn)
        self.setup()
