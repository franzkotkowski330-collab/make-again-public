"""结构化输出:从模型文本里稳健提取 JSON。"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json(text: str) -> tuple[Any | None, str]:
    """返回 (解析出的对象或 None, 原始文本)。

    容错:去掉 markdown 代码块围栏,再用括号配对找到所有平衡的 {...} 片段(长的优先),
    逐个尝试 json.loads,并清理尾逗号。可应对:嵌套 JSON、围栏、前后杂散文字、
    推理模型偶发的尾逗号/序言等。
    """
    if not text:
        return None, text
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
    for candidate in _balanced_json_blocks(cleaned):
        for cand in (candidate, _drop_trailing_commas(candidate)):
            try:
                return json.loads(cand), text
            except json.JSONDecodeError:
                continue
    return None, text


def _balanced_json_blocks(text: str) -> list[str]:
    """返回文本中所有「从 { 开始、括号配平」的片段,长的在前(顶层对象最长)。"""
    blocks: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[i:j + 1])
                    break
        i += 1
    blocks.sort(key=len, reverse=True)
    return blocks


def _drop_trailing_commas(text: str) -> str:
    """去掉对象/数组末尾的尾逗号(如 {...,} 或 [...,])。"""
    return re.sub(r",(\s*[}\]])", r"\1", text)
