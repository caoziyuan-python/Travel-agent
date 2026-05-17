"""
小红书检索增强回答（Grounded Answer）

流程：
1) 调用 search_notes 检索小红书内容
2) 解析为结构化笔记
3) 按长度切块并做简单相关性排序
4) 将证据块发送给 GPT，生成可直接给前端展示的回答
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from agent.tools.xhs_tools import call_xhs_tool
from utils.config import (
    AZURE_API_KEY,
    AZURE_BASE_URL,
    AZURE_DEPLOYMENT,
    AGENT_TEMPERATURE,
    should_send_temperature,
)


@dataclass
class XHSNote:
    title: str
    author: str
    content: str
    likes: str
    comments: str
    url: str


@dataclass
class EvidenceChunk:
    source_id: int
    title: str
    author: str
    url: str
    likes: str
    comments: str
    text: str
    score: int


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=AZURE_API_KEY, base_url=AZURE_BASE_URL)


def parse_search_notes_text(raw_text: str) -> list[XHSNote]:
    """把 rednote-mcp 的纯文本搜索结果解析为结构化笔记。"""
    if not raw_text.strip():
        return []

    blocks = [b.strip() for b in raw_text.split("\n---\n") if b.strip()]
    notes: list[XHSNote] = []

    for block in blocks:
        title = ""
        author = ""
        content_lines: list[str] = []
        likes = ""
        comments = ""
        url = ""
        in_content = False

        for line in block.splitlines():
            s = line.strip()
            if s.startswith("标题:"):
                in_content = False
                title = s.removeprefix("标题:").strip()
                continue
            if s.startswith("作者:"):
                in_content = False
                author = s.removeprefix("作者:").strip()
                continue
            if s.startswith("内容:"):
                in_content = True
                content_lines.append(s.removeprefix("内容:").strip())
                continue
            if s.startswith("点赞:"):
                in_content = False
                likes = s.removeprefix("点赞:").strip()
                continue
            if s.startswith("评论:"):
                in_content = False
                comments = s.removeprefix("评论:").strip()
                continue
            if s.startswith("链接:"):
                in_content = False
                url = s.removeprefix("链接:").strip()
                continue
            if in_content:
                content_lines.append(s)

        content = "\n".join([x for x in content_lines if x]).strip()
        if title or content or url:
            notes.append(
                XHSNote(
                    title=title,
                    author=author,
                    content=content,
                    likes=likes,
                    comments=comments,
                    url=url,
                )
            )

    return notes


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _query_terms(query: str) -> list[str]:
    """构建简易中英混合检索词。"""
    q = _normalize(query)
    if not q:
        return []

    terms: set[str] = {q}
    # 英文词
    for w in re.findall(r"[a-z0-9]+", q):
        if len(w) >= 2:
            terms.add(w)
    # 中文 2-gram
    zh = re.sub(r"[^\u4e00-\u9fff]", "", q)
    for i in range(max(0, len(zh) - 1)):
        terms.add(zh[i : i + 2])
    return sorted(terms, key=len, reverse=True)


def _score_text(text: str, query: str) -> int:
    t = _normalize(text)
    terms = _query_terms(query)
    score = 0
    for term in terms:
        if not term:
            continue
        score += t.count(term)
    return score


def build_evidence_chunks(
    notes: list[XHSNote],
    query: str,
    *,
    max_chunk_chars: int = 900,
    overlap_chars: int = 120,
    top_k: int = 6,
) -> list[EvidenceChunk]:
    """按单篇笔记切块并做简单排序，返回前 top_k。"""
    chunks: list[EvidenceChunk] = []
    source_id = 1

    for note in notes:
        content = note.content.strip()
        if not content:
            continue

        prefix = f"标题: {note.title}\n作者: {note.author}\n点赞: {note.likes} 评论: {note.comments}\n"
        room = max(200, max_chunk_chars - len(prefix))

        if len(content) <= room:
            text = f"{prefix}内容: {content}"
            chunks.append(
                EvidenceChunk(
                    source_id=source_id,
                    title=note.title,
                    author=note.author,
                    url=note.url,
                    likes=note.likes,
                    comments=note.comments,
                    text=text,
                    score=_score_text(text, query),
                )
            )
            source_id += 1
            continue

        start = 0
        while start < len(content):
            end = min(len(content), start + room)
            part = content[start:end]
            text = f"{prefix}内容片段: {part}"
            chunks.append(
                EvidenceChunk(
                    source_id=source_id,
                    title=note.title,
                    author=note.author,
                    url=note.url,
                    likes=note.likes,
                    comments=note.comments,
                    text=text,
                    score=_score_text(text, query),
                )
            )
            source_id += 1
            if end >= len(content):
                break
            start = max(0, end - overlap_chars)

    # score 降序，点赞作为弱排序信号（点赞可能为空）
    chunks.sort(
        key=lambda c: (
            c.score,
            int(c.likes) if c.likes.isdigit() else 0,
        ),
        reverse=True,
    )
    return chunks[: max(1, top_k)]


def _evidence_to_prompt(chunks: list[EvidenceChunk]) -> str:
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        parts.append(
            "\n".join(
                [
                    f"[{i}] 标题: {c.title}",
                    f"[{i}] 作者: {c.author}",
                    f"[{i}] 链接: {c.url}",
                    f"[{i}] 证据: {c.text}",
                ]
            )
        )
    return "\n\n".join(parts)


async def generate_grounded_answer(
    *,
    user_query: str,
    chunks: list[EvidenceChunk],
    extra_prompt: str = "",
) -> str:
    if not chunks:
        return "没有检索到可用的小红书内容，请换一个关键词再试。"

    client = _build_client()
    evidence_text = _evidence_to_prompt(chunks)

    system_prompt = (
        "你是北京旅行攻略助手。"
        "请严格基于给定证据回答，不要编造。"
        "回答要中文、清晰、可执行。"
        "引用观点时在句末标注来源编号，如 [1] [2]。"
        "若证据不足，要明确说“证据不足”。"
    )
    if extra_prompt.strip():
        system_prompt += "\n补充要求:\n" + extra_prompt.strip()

    user_prompt = (
        f"用户问题：{user_query}\n\n"
        "请输出：\n"
        "1) 结论（先给3-6条可执行建议）\n"
        "2) 注意事项\n"
        "3) 参考来源（列出标题+链接）\n\n"
        f"证据如下：\n{evidence_text}"
    )

    req: dict[str, Any] = {
        "model": AZURE_DEPLOYMENT,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if should_send_temperature(AZURE_DEPLOYMENT):
        req["temperature"] = AGENT_TEMPERATURE

    resp = await client.chat.completions.create(**req)
    return (resp.choices[0].message.content or "").strip() or "模型未返回内容。"


async def search_notes_and_answer(
    *,
    query: str,
    top_k: int = 6,
    max_chunk_chars: int = 900,
    overlap_chars: int = 120,
    search_limit: int = 5,
    extra_prompt: str = "",
) -> dict[str, Any]:
    """
    对外统一入口：
    search_notes -> parse -> chunk/rank -> GPT 回答
    """
    raw_result = await call_xhs_tool("search_notes", {"keywords": query, "limit": search_limit})
    notes = parse_search_notes_text(raw_result)
    chunks = build_evidence_chunks(
        notes,
        query,
        max_chunk_chars=max_chunk_chars,
        overlap_chars=overlap_chars,
        top_k=top_k,
    )
    answer = await generate_grounded_answer(
        user_query=query,
        chunks=chunks,
        extra_prompt=extra_prompt,
    )

    sources = [
        {
            "rank": i + 1,
            "title": c.title,
            "author": c.author,
            "url": c.url,
            "likes": c.likes,
            "comments": c.comments,
            "score": c.score,
        }
        for i, c in enumerate(chunks)
    ]

    return {
        "query": query,
        "answer": answer,
        "notes_count": len(notes),
        "chunks_used": len(chunks),
        "sources": sources,
    }
