"""
环境变量配置
在项目根目录或 backend/ 目录下创建 .env 文件
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Azure OpenAI（由 xhs 工具层与 local_rednote_agent 统一使用）
AZURE_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")


def build_azure_openai_base_url(endpoint: str = AZURE_ENDPOINT) -> str:
    """
    Return an OpenAI SDK compatible Azure base URL.

    Accept both the resource root (`https://name.openai.azure.com`) and the v1
    endpoint (`https://name.openai.azure.com/openai/v1`) to avoid accidentally
    calling `/openai/v1/openai/v1`.
    """
    endpoint = endpoint.rstrip("/")
    if not endpoint:
        return ""
    if endpoint.endswith("/openai/v1"):
        return endpoint + "/"
    return endpoint + "/openai/v1/"


AZURE_BASE_URL: str = build_azure_openai_base_url()


def is_gpt5_family(model_name: str) -> bool:
    return model_name.strip().lower().startswith("gpt-5")


def should_send_temperature(model_name: str) -> bool:
    """
    GPT-5 family on Azure/OpenAI v1 currently only accepts default temperature.
    Omit the field to avoid 400 unsupported_value errors.
    """
    return not is_gpt5_family(model_name)

# 高德地图
AMAP_API_KEY: str = os.getenv("AMAP_API_KEY", "")

# RAG 静态知识库（向量嵌入模型部署名）
AZURE_EMBEDDING_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

# 价格统一货币配置（机票/酒店）
PRICE_CURRENCY: str = os.getenv("PRICE_CURRENCY", "CNY").strip().upper()
USD_CNY_RATE: float = float(os.getenv("USD_CNY_RATE", "7.2"))
LIVE_FX_ENABLED: bool = os.getenv("LIVE_FX_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
FX_TIMEOUT_SECONDS: float = float(os.getenv("FX_TIMEOUT_SECONDS", "5"))

# rednote-mcp 进程启动参数
MCP_COMMAND: str = os.getenv("MCP_COMMAND", "npx")
MCP_ARGS: list[str] = os.getenv("MCP_ARGS", "rednote-mcp --stdio").split()

# Agent 行为
AGENT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "0.2"))
MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", "8"))
OPENAI_TIMEOUT_SECONDS: float = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))

# 上下文管理
CONTEXT_RECENT_MESSAGES: int = int(os.getenv("CONTEXT_RECENT_MESSAGES", "12"))
CONTEXT_SUMMARY_TRIGGER_MESSAGES: int = int(os.getenv("CONTEXT_SUMMARY_TRIGGER_MESSAGES", "20"))
CONTEXT_STORE: str = os.getenv("CONTEXT_STORE", "memory").strip().lower()
