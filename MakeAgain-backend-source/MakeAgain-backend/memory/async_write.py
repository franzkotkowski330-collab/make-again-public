"""后台记忆回写:把"抽取事实/情绪 + 落库"移到后台线程,让主对话先返回。

设计要点:
- 主对话(陪伴 Agent)只同步做 危机检测 → 召回 → 生成回复,这三步是用户等待的关键路径。
- 记忆抽取(2 次 LLM 调用)与落库放到单 worker 后台线程,不阻塞回复。
- 单 worker 串行化写库,避免 SQLite 并发写锁竞争;后台任务使用独立的 DB 会话与 LLM 客户端。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from gateway.client import LLMClient
from memory.db import SessionLocal

logger = logging.getLogger(__name__)

# 单 worker:串行执行回写,避免并发写 SQLite 争锁
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem-write")


def schedule_memory_write(user_id: str, message: str, *, type: str = "chat", extractor=None) -> None:
    """提交一次后台回写,立即返回。抽取失败不影响主流程。

    extractor 为可选的自定义抽取函数 `f(client, text) -> turn dict`,
    用于按场景替换抽取逻辑(如失恋场景的 CBT 抽取);缺省走通用抽取。
    """
    from memory.jobs import enqueue
    enqueue("memory",user_id,{"message":message,"type":type,"extractor":extractor.__module__ if extractor else None})


def _do_write(user_id: str, message: str, type: str, extractor=None) -> None:
    from memory import extract, portrait, reflect, store

    db = SessionLocal()
    try:
        client = LLMClient()  # 独立客户端,避免与请求线程共享 httpx 连接
        if extractor is not None:
            turn = extractor(client, message)
        else:
            turn = extract.extract_turn(client, message)
        entry = store.add_memory(
            db, user_id, type=type, content=message, summary=turn["summary"],
            facts=turn["facts"], emotion=turn["emotion"], importance=turn["importance"],
            place_tag=turn["place_tag"], time_tag=turn["time_tag"],
            cbt=turn.get("cbt"),
        )
        # 增量更新情绪节点(替代每轮全量重建),让情绪节点随聊天自动更新。
        reflect.upsert_emotion_node(db, entry)
        # 回声壁:随机捕获富有情感的句子,随机延时后回显(零 LLM、纯采样,不阻塞主流程)
        from agent import echo as echo_mod
        echo_mod.maybe_capture_echo(db, user_id, message, turn.get("emotion"))
        # 积累足够新事实后,后台增量合并进画像(不进热路径)
        from memory.jobs import enqueue
        enqueue("portrait",user_id,{})
    except Exception:  # noqa: BLE001 后台任务必须兜底,不能把异常抛回请求
        raise
    finally:
        db.close()


def flush_memory_writes() -> None:
    """等待当前已提交的回写全部完成(供脚本/测试在退出前调用,确保落库)。"""
    _executor.submit(lambda: None).result()
