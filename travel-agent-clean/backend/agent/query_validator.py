"""
用户需求验证模块

功能：
  1. 检查用户需求是否在产品功能范围内
  2. 识别超出范围的请求并拒绝回答
  3. 识别用户情绪并支持客服升级
  4. 整理用户需求信息供后续工具链使用
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class UserEmotion(str, Enum):
    """用户情绪分类"""
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    NEUTRAL = "neutral"


class UserIntent(str, Enum):
    """用户意图分类"""
    # 旅行范围内的意图
    SEARCH_ATTRACTIONS = "search_attractions"
    SEARCH_RESTAURANTS = "search_restaurants"
    SEARCH_HOTELS = "search_hotels"
    SEARCH_FLIGHTS = "search_flights"
    GET_WEATHER = "get_weather"
    GET_TRANSIT_ROUTE = "get_transit_route"
    GET_TRAVEL_GUIDE = "get_travel_guide"
    PLAN_ITINERARY = "plan_itinerary"
    INQUIRE = "inquire"

    # 旅行范围外的意图
    BOOK_FLIGHT = "book_flight"
    CANCEL_FLIGHT = "cancel_flight"
    CHANGE_FLIGHT = "change_flight"
    BOOK_HOTEL = "book_hotel"
    CANCEL_BOOKING = "cancel_booking"
    COMPLAINT = "complaint"


class QueryAnalysisResult:
    """用户需求分析结果"""

    def __init__(self, original_query: str, emotion: str = UserEmotion.NEUTRAL, intent: str = UserEmotion.NEUTRAL, requirements: Optional[str] = None, preferences: Optional[str] = None, raw_result: Optional[dict] = None):
        self.original_query = original_query
        self.emotion = emotion
        self.intent = intent
        self.requirements = requirements
        self.preferences = preferences
        self.raw_result = raw_result or {}

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "emotion": self.emotion,
            "intent": self.intent,
            "requirements": self.requirements,
            "preferences": self.preferences,
        }

    def __repr__(self) -> str:
        return (
            f"QueryAnalysisResult("
            f"query={{self.original_query!r}}, "
            f"emotion={{self.emotion}}, "
            f"intent={{self.intent}})"
        )


class QueryValidator:
    """用户需求验证器"""

    # 功能范围内的意图（白名单）
    ALLOWED_INTENTS = {
        UserIntent.SEARCH_ATTRACTIONS,
        UserIntent.SEARCH_RESTAURANTS,
        UserIntent.SEARCH_HOTELS,
        UserIntent.SEARCH_FLIGHTS,
        UserIntent.GET_WEATHER,
        UserIntent.GET_TRANSIT_ROUTE,
        UserIntent.GET_TRAVEL_GUIDE,
        UserIntent.PLAN_ITINERARY,
        UserIntent.INQUIRE,
    }

    # 功能范围外的意图（黑名单）
    OUT_OF_SCOPE_INTENTS = {
        UserIntent.BOOK_FLIGHT,
        UserIntent.CANCEL_FLIGHT,
        UserIntent.CHANGE_FLIGHT,
        UserIntent.BOOK_HOTEL,
        UserIntent.CANCEL_BOOKING,
        UserIntent.COMPLAINT,
    }

    @staticmethod
    def validate(analysis: QueryAnalysisResult) -> tuple[bool, str, Optional[str]]:
        """
        验证用户需求

        Args:
            analysis: 需求分析结果（来自 Customer Query Server MCP）

        Returns:
            (is_valid: bool, message: str, escalation_reason: Optional[str])
            - is_valid: 需求是否在功能范围内
            - message: 回复给用户的信息（如果不在范围内）
            - escalation_reason: 如需升级处理的原因（如识别到愤怒情绪）
        """
        intent = analysis.intent
        emotion = analysis.emotion

        logger.info(
            f"验证用户需求: emotion={{emotion}}, intent={{intent}}, "
            f"query={{analysis.original_query!r}}"
        )

        # 1. 检查意图是否超出范围
        if intent in [i.value for i in QueryValidator.OUT_OF_SCOPE_INTENTS]:
            reason = QueryValidator._get_out_of_scope_message(intent)
            logger.warning(f"用户需求超出功能范围: {{intent}}")
            return False, reason, None

        # 2. 检查用户情绪，识别需要升级的情况
        if emotion == UserEmotion.ANGRY or emotion == "angry":
            logger.warning(f"识别到用户愤怒情绪，建议升级处理")
            return (
                True,
                "我感受到了您的不满。我会尽力帮助，但如果我无法解决您的问题，可以为您转接客服专员。",
                "user_angry",
            )

        return True, "", None

    @staticmethod
    def _get_out_of_scope_message(intent: str) -> str:
        """根据意图类型返回对应的拒绝信息"""
        messages = {
            UserIntent.BOOK_FLIGHT: (
                "抱歉，我无法处理机票预订。我是一个旅行规划助手，主要帮助你了解景点、餐厅、天气和路线规划。"
                "如需机票预订，请访问 www.example.com 或联系我们的客服团队。"
            ),
            UserIntent.CANCEL_FLIGHT: (
                "抱歉，我无法处理机票取消。请登录你的账户或联系客服处理退票事务。"
            ),
            UserIntent.CHANGE_FLIGHT: (
                "抱歉，我无法处理机票改签。请登录你的账户或直接拨打客服电话改签。"
            ),
            UserIntent.BOOK_HOTEL: (
                "抱歉，我无法处理酒店预订。我可以帮你搜索北京的酒店信息和位置，"
                "但预订请通过 booking.com、携程等平台进行。"
            ),
            UserIntent.CANCEL_BOOKING: (
                "抱歉，我无法处理订单取消。请登录原预订平台或联系平台客服。"
            ),
            UserIntent.COMPLAINT: (
                "我很遗憾听到你的不满。如有投诉或建议，请联系我们的客服团队，"
                "我们会认真处理你的反馈。"
            ),
        }
        return messages.get(
            intent,
            f"抱歉，我无法处理这类请求。有其他旅行相关的问题吗？",
        )

    @staticmethod
    def enrich_system_prompt(analysis: QueryAnalysisResult) -> str:
        """
        根据需求分析结果生成增强的系统提示

        用于注入到 LLM 上下文中，帮助模型更精准地调用工具

        Args:
            analysis: 需求分析结果

        Returns:
            增强系统提示的文本片段
        """
        enriched = (
            f"\n---\n"
            f"本次用户需求分析（由 Customer Query Server 提供）:\n"
            f"- 原始问题: {{analysis.original_query}}\n"
            f"- 用户情绪: {{analysis.emotion}}\n"
            f"- 主要意图: {{analysis.intent}}\n"
        )

        if analysis.requirements:
            enriched += f"- 需求等级: {{analysis.requirements}}\n"

        if analysis.preferences:
            enriched += f"- 用户偏好: {{analysis.preferences}}\n"

        enriched += (
            f"请基于以上分析结果，使用相关工具提供最精准的旅行建议。\n"
            f"---\n"
        )

        return enriched


# ── 便捷函数 ────────────────────────────────────────────────────────────────

def validate_query(analysis: QueryAnalysisResult) -> tuple[bool, str, Optional[str]]:
    """便捷函数：验证单个查询

    Returns:
        (is_valid, message, escalation_reason)
    """
    return QueryValidator.validate(analysis)


def is_in_scope(intent: str) -> bool:
    """检查意图是否在功能范围内"""
    return intent in [i.value for i in QueryValidator.ALLOWED_INTENTS]


def is_out_of_scope(intent: str) -> bool:
    """检查意图是否超出功能范围"""
    return intent in [i.value for i in QueryValidator.OUT_OF_SCOPE_INTENTS]