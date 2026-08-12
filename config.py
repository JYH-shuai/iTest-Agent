"""
iTest-Agent 配置文件

集中管理环境变量、模型参数、路径等配置。
支持从 .env 文件加载。
"""

import os
from dataclasses import dataclass, field
from typing import Optional

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class ITestConfig:
    """iTest-Agent 全局配置"""

    # ---- LLM 配置 ----
    llm_model: str = field(
        default_factory=lambda: os.getenv("ITEST_LLM_MODEL", "gpt-4o-mini")
    )
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("ITEST_LLM_TEMPERATURE", "0.1"))
    )
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("ITEST_LLM_MAX_TOKENS", "4096"))
    )

    # ---- API Key ----
    openai_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    openai_base_url: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL")
    )

    # ---- 知识库 ----
    kb_persist_dir: str = field(
        default_factory=lambda: os.getenv("ITEST_KB_DIR", "./chroma_db")
    )
    kb_collection_name: str = field(
        default_factory=lambda: os.getenv("ITEST_KB_COLLECTION", "itest_knowledge")
    )

    # ---- Checkpoint ----
    checkpoint_db_path: str = field(
        default_factory=lambda: os.getenv(
            "ITEST_CHECKPOINT_DB", "./itest_checkpoints.db"
        )
    )

    # ---- 输出 ----
    output_dir: str = field(
        default_factory=lambda: os.getenv("ITEST_OUTPUT_DIR", "./output")
    )

    # ---- 工作流 ----
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("ITEST_MAX_RETRIES", "2"))
    )
    review_pass_threshold: float = field(
        default_factory=lambda: float(os.getenv("ITEST_REVIEW_THRESHOLD", "70.0"))
    )

    # ---- MCP Server ----
    mcp_playwright_url: Optional[str] = field(
        default_factory=lambda: os.getenv("ITEST_MCP_PLAYWRIGHT_URL")
    )
    mcp_api_test_url: Optional[str] = field(
        default_factory=lambda: os.getenv("ITEST_MCP_API_TEST_URL")
    )


# 全局单例
_config: Optional[ITestConfig] = None


def get_config() -> ITestConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = ITestConfig()
    return _config
