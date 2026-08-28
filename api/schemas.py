"""
FastAPI 接口数据模型（Pydantic Schema）
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineResponse(BaseModel):
    """触发流水线后的响应"""

    task_id: str = Field(..., description="任务 ID")
    status: str = Field(..., description="任务初始状态")
    detail: str = Field("", description="提示信息")


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""

    task_id: str
    status: str  # pending / running / completed / failed
    phase: str = ""
    created_at: str = ""
    updated_at: str = ""
    prd_filename: str = ""
    incremental: bool = False
    analysis: Dict[str, Any] = Field(default_factory=dict)
    test_suite: Dict[str, Any] = Field(default_factory=dict)
    review: Dict[str, Any] = Field(default_factory=dict)
    execution: Dict[str, Any] = Field(default_factory=dict)
    report_path: str = ""
    traceability_path: str = ""
    phase_times: Dict[str, Any] = Field(default_factory=dict)
    messages: List[str] = Field(default_factory=list)
    error: str = ""


class IncrementalRequest(BaseModel):
    """增量更新请求"""

    changed_function_ids: List[str] = Field(
        ..., min_length=1, description="变更的功能 ID 列表（子功能 ID）"
    )
    change_type: str = Field("incremental", description="变更类型")


class PipelineOptions(BaseModel):
    """流水线可选参数（表单字段）"""

    model: str = Field("gpt-4o-mini", description="LLM 模型名称")
    execution_mode: str = Field(
        "simulated", description="执行模式: mcp / simulated"
    )
    mock_llm: bool = Field(False, description="是否启用规则解析降级（无 API Key）")
    max_review_rounds: int = Field(3, ge=1, le=10, description="评审最大迭代次数")
    sync: bool = Field(False, description="是否同步等待执行完成")
    api_base_url: str = Field("", description="接口测试默认 Base URL")
