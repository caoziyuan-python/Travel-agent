"""
Customer Query Server MCP 客户端

用途：
  通过 MCP 协议连接到 Customer Query Server，调用 analyze_customer_query 工具
  分析用户需求，理解用户意图、情绪、需求等级，并整理后续工具调用的信息

架构：
  本模块基于 aiohttp 或 httpx 实现 HTTP MCP 传输
  与 xhs_tools.py 类似的单例模式维护连接生命周期
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class CustomerQueryMCPClient:
    """Customer Query Server MCP 客户端"""

    def __init__(self,
        server_url: str = "http://localhost:8000/mcp",
        timeout: int = 30,
    ):
        """
        初始化 MCP 客户端

        Args:
            server_url: Customer Query Server 的 MCP 端点 URL
                       格式: http://host:port/mcp (根据 Program.cs 中 MapMcp("/mcp") 配置)
            timeout: 请求超时时间（秒）
        """
        self.server_url = server_url
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """建立连接"""
        self.client = httpx.AsyncClient(timeout=self.timeout)
        logger.info(f"Customer Query Server MCP 客户端已初始化，服务地址: {self.server_url}")

    async def close(self) -> None:
        """关闭连接"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("Customer Query Server MCP 客户端连接已关闭")

    async def analyze_query(self, user_query: str) -> dict:
        """
        调用 analyze_customer_query 工具分析用户需求

        Args:
            user_query: 用户输入的原始问题

        Returns:
            {
                "customer_query": str,      # 原始用户问题
                "emotion": str,             # 用户情绪: happy, sad, angry, neutral
                "intent": str,              # 用户意图: book_flight, cancel_flight, change_flight, inquire, complaint
                "requirements": str,        # 需求等级: business, economy, first_class
                "preferences": str          # 用户偏好: window, aisle, extra_legroom
            }

        Raises:
            ConnectionError: MCP 服务器连接失败
            ValueError: 解析响应失败
        """
        if not self.client:
            raise ConnectionError("MCP 客户端未连接，请先调用 connect() 方法")

        try:
            # 根据 MCP 协议构造请求
            # 使用 HTTP POST 调用 analyze_customer_query 工具
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "analyze_customer_query",
                    "arguments": {"customerQuery": user_query},
                },
            }

            response = await self.client.post(
                self.server_url,
                json=payload,
            )

            if response.status_code != 200:
                logger.error(
                    f"MCP 服务返回错误: {response.status_code} {response.text}"
                )
                raise ValueError(f"MCP 服务错误: {response.status_code}")

            result = response.json()

            # 处理 MCP 响应
            if "error" in result:
                raise ValueError(f"MCP 错误响应: {result['error']}")

            # 提取工具调用结果
            tool_result = result.get("result", {})
            if isinstance(tool_result, dict):
                return tool_result
            elif isinstance(tool_result, str):
                # 如果是字符串，尝试 JSON 解析
                return json.loads(tool_result)
            else:
                raise ValueError(f"未预期的结果格式: {type(tool_result)}")

        except httpx.ConnectError as e:
            logger.error(f"无法连接到 Customer Query Server: {e}")
            raise ConnectionError(
                f"无法连接到 Customer Query Server ({self.server_url}): {e}"
            )
        except httpx.TimeoutException as e:
            logger.error(f"Customer Query Server 请求超时: {e}")
            raise TimeoutError(f"Customer Query Server 请求超时: {e}")
        except Exception as e:
            logger.error(f"Customer Query Server 调用失败: {e}")
            raise


# ── 模块级单例 ─────────────────────────────────────────────────────────────

_client: Optional[CustomerQueryMCPClient] = None


async def init_customer_query_client(
    server_url: str = "http://localhost:8000/mcp",
    timeout: int = 30,
) -> None:
    """
    初始化全局 Customer Query MCP 客户端

    在 FastAPI lifespan 的启动阶段调用

    Args:
        server_url: Customer Query Server 的 MCP 端点
        timeout: 请求超时时间
    """
    global _client
    _client = CustomerQueryMCPClient(server_url, timeout)
    await _client.connect()
    logger.info("✓ Customer Query Server MCP 已连接")


async def close_customer_query_client() -> None:
    """
    关闭 Customer Query MCP 客户端

    在 FastAPI lifespan 的关闭阶段调用
    """
    global _client
    if _client:
        await _client.close()
        _client = None


async def analyze_customer_query(user_query: str) -> Optional[dict]:
    """
    便捷函数：直接调用客户端分析用户需求

    如果客户端未初始化或调用失败，返回 None
    """
    if not _client:
        logger.warning("Customer Query Server MCP 未初始化")
        return None

    try:
        return await _client.analyze_query(user_query)
    except Exception as e:
        logger.warning(f"Customer Query 分析失败，降级处理: {e}")
        return None
