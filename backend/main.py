"""
北京 AI 旅行助手 — FastAPI 后端入口

启动方式（从 backend/ 目录）：
    uvicorn main:app --reload --port 8000

API 文档：
    http://localhost:8000/docs

环境变量（.env 文件，放在项目根目录或 backend/ 目录）：
    AZURE_OPENAI_ENDPOINT    = https://your-resource.openai.azure.com
    AZURE_OPENAI_API_KEY     = your_key
    AZURE_OPENAI_DEPLOYMENT  = your_deployment_name
    AMAP_API_KEY             = your_amap_key
    MCP_COMMAND              = npx          # 默认
    MCP_ARGS                 = rednote-mcp --stdio   # 默认
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils.config import MCP_COMMAND, MCP_ARGS
from agent.tools.xhs_tools import init_xhs_client, close_xhs_client
from api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理：
    - 启动时连接 rednote-mcp，加载小红书工具
    - 关闭时断开 MCP 连接
    前提：npm install -g rednote-mcp && rednote-mcp init（登录小红书）
    """
    print("[Startup] 正在连接 rednote-mcp...")
    try:
        await init_xhs_client(MCP_COMMAND, MCP_ARGS)
    except Exception as e:
        print(f"[Startup] 警告：小红书 MCP 连接失败（{e}），XHS 工具不可用，其他功能正常运行")
    yield
    print("[Shutdown] 正在关闭 MCP 连接...")
    await close_xhs_client()


app = FastAPI(
    title="北京 AI 旅行助手",
    description="基于 Azure OpenAI + 高德地图 + 小红书 (scripts/xiaohongshu/local_rednote_agent) 的北京旅行智能助手",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境替换为前端实际域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1", tags=["travel"])


@app.get("/", tags=["health"])
async def root():
    return {"message": "北京 AI 旅行助手后端运行中", "docs": "/docs", "api": "/api/v1"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
