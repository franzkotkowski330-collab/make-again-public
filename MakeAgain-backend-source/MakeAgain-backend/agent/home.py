"""主界面聚合:一次返回情绪日历 + 软引导 + 今日主题,减少前端三次往返。

进入主界面(引导阶段 = main)时,前端调 /api/home/{user_id} 即可拿到三要素:
- calendar:情绪日历(零 LLM)
- nudges:当前软引导(无触发时自动回落为通用语录)
- themes:今日个性化主题(零 LLM)
"""
from __future__ import annotations

from agent import daily as daily_mod, nudge as nudge_mod
from gateway.client import LLMClient
from memory import calendar as calendar_mod, store


def get_home(db, user_id: str, client: LLMClient) -> dict:
    store.get_or_create_user(db, user_id)
    return {
        "calendar": calendar_mod.get_calendar(db, user_id),
        "nudges": nudge_mod.get_nudges(db, user_id, client),
        "themes": daily_mod.get_themes(db, user_id),
    }
