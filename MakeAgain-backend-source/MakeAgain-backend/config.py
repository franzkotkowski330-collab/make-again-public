"""全局配置:环境变量驱动,`.env` 可覆盖。"""
import os

# 本后端只访问白名单内的国内服务(DeepSeek / 腾讯云 MaaS·COS·数据万象 / gitee),均为直连可达。
# 关闭 HTTP 客户端对系统代理的继承:Windows 系统代理(如 127.0.0.1:7897)会把请求引到
# 失效的本地代理上,导致 httpx / requests(qcloud_cos)报 ProxyError,连不上任何外部服务。
# 必须在导入任何 httpx / requests / qcloud_cos 之前设置(gateway 模块均在 import config 之后导入)。
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    cors_origins: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    media_signing_key: str = ""
    mock_storage: bool = False
    wechat_pay_mchid: str = ""
    wechat_pay_serial_no: str = ""
    wechat_pay_private_key_path: str = ""
    wechat_pay_platform_key_path: str = ""
    wechat_pay_platform_serial: str = ""
    wechat_pay_api_v3_key: str = ""
    wechat_pay_notify_url: str = ""
    payment_allowed_platforms: str = ""
    wechat_subscribe_templates: str = "[]"
    # 数据库
    database_url: str = "sqlite:///./data/app.db"

    # LLM 网关
    llm_base_url: str = "https://api.deepseek.com/v1"  # OpenAI 兼容
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-pro"       # 冷路径推理模型(可选的高质量档,默认不用于在线报告)
    llm_fast_model: str = "deepseek-chat"    # 热路径:非推理,延迟优先(陪伴/访谈决策/报告/抽取均走此)
    llm_report_model: str = ""               # 报告生成模型;空则用 llm_fast_model(时延优先),可设为 llm_model 追求质量
    judge_model: str = ""                    # 评估 judge 模型;空则用 llm_fast_model
    llm_timeout: float = 60.0

    # 无 key 或显式开启时走 mock,便于本地 smoke test
    mock_llm: bool = False

    # 微信小程序登录(jscode2session → openid)
    wechat_appid: str = ""
    wechat_secret: str = ""
    wechat_code2session_url: str = "https://api.weixin.qq.com/sns/jscode2session"
    wechat_timeout: float = 10.0
    mock_wechat: bool = False   # 无 appid/secret 或显式开启时走 mock,便于上线前离线测试

    # 语音(TTS / ASR,经腾讯 MaaS 代理转发 MiniMax / 混元)
    maas_base_url: str = "https://tokenhub.tencentmaas.com"
    maas_api_key: str = ""
    tts_model: str = "minimax-speech-2.8-hd"
    tts_voice_id: str = "female-shaonv"
    asr_model: str = "hy-asr-3.0-preview"
    speech_timeout: float = 120.0
    mock_speech: bool = False
    speech_max_upload_mb: int = 20   # 语音上传大小上限

    # 对象存储(腾讯云 COS;未配凭据时本地目录兜底,仅跑通链路)
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = "ap-guangzhou"
    cos_bucket: str = ""               # 形如 bucketname-1250000000
    cos_upload_prefix: str = "voice"
    cos_presign_expires: int = 600     # 预签名 URL 有效期(秒)
    cos_timeout: float = 15.0          # COS SDK 单次请求超时(秒);配合 retry=1,不可达时约 30s 内失败
    cos_use_old_domain: bool = False   # False=新域名 tencentcos.cn(本机可达);True=旧域名 myqcloud.com(本机被墙/白名单未放行)
    storage_local_dir: str = "data/uploads"

    # 物品纪念/寄存(聊天 tool calling 的第一个工具)
    item_max_upload_mb: int = 10       # 物品图片上传大小上限
    cos_item_prefix: str = "item"      # 物品图片在对象存储里的前缀
    cos_photo_prefix: str = "photo"    # 场景照片(拍立得)在对象存储里的前缀
    item_verify_cutout: bool = False   # 抠图后是否多模态校验结果(约 +5s);关=更快,默认关

    # 账号 / 会员(激活码兑换,暂不门禁功能)
    activation_codes: str = ""          # 逗号分隔的合法激活码(如 ABC123,DEF456),空=无有效码

    # 视觉(腾讯云图像:识别物品 + 主体分割抠图;空凭据则回退 cos 凭据,mock 下走兜底)
    vision_secret_id: str = ""
    vision_secret_key: str = ""
    vision_region: str = "ap-guangzhou"
    vision_llm_model: str = "hy-vision-2.0-instruct"   # 定位(grounding)用的多模态模型(TokenHub)
    mock_vision: bool = False

    # 时区偏移(软引导"晚上别熬夜"等依赖本地时间;默认东八区)
    timezone_offset_hours: int = 8

    # 每日主题:每天推荐的主题数量
    daily_theme_count: int = 3

    # 树洞信箱
    treehole_write_sessions: int = 2      # 写信门槛:实际完成的对话轮数(旧配置名称保留，稳定会话 ID 不再决定资格)
    treehole_reply_min_days: int = 7      # 回信门槛:至少累积的天数(保证情绪数据足够)
    treehole_reply_stable_score: float = 55.0  # 近 7 天情绪均分阈值(暂定,后续随"情绪数据化"细化)
    treehole_match_top_k: int = 5         # 结构化初筛后交给 LLM 复排的候选数
    treehole_match_top_n: int = 3         # 最终返回给用户的来信数
    treehole_operator_id: str = "operator"  # 运营人员回信时写入 author_user_id 的占位,用于区分"用户回信"与"官方回信"

    # 定期跟踪报告
    report_min_days: int = 7              # 数据充足:至少累积天数
    report_min_memories: int = 15         # 数据充足:至少倾诉条数
    report_max_cards: int = 4             # 每份报告最多卡片数(含总结分析)

    # 记忆召回
    recall_top_k: int = 8
    recall_alpha: float = 0.35   # recency 权重
    recall_beta: float = 0.35    # importance 权重
    recall_gamma: float = 0.30   # relevance 权重
    # 记忆引用克制:记忆自身词汇中已有该比例出现在最近对话里,就视为"正在被聊",不再注入(避免刚说完又提)
    recall_recent_overlap_threshold: float = 0.5

    # 临时会话上下文(进程内内存字典兜底)
    session_context_ttl_minutes: int = 30   # 无活动超过此时长视为退出,读取时惰性清空
    session_context_max_turns: int = 6      # 保留最近 N 对(user+assistant)
    session_context_max_chars: int = 300    # 单条消息最大字符数

    # 画像增量合并
    portrait_merge_every: int = 20          # 画像 updated_at 之后累积这么多条记忆才考虑合并
    portrait_merge_min_confidence: float = 0.7  # 参与合并的 fact 最低置信度

    # 聊天历史(精确到分钟,游标分页)
    chat_history_page_size: int = 30        # 每页默认条数
    chat_history_max_page_size: int = 100   # 每页条数上限

    # 聊天记录每日总结(退出写草稿,跨天惰性固定)
    chat_daily_summary_max_chars: int = 60        # 总结长度上限(一句话)
    chat_daily_summary_transcript_chars: int = 2400  # 喂给 LLM 的当天 transcript 上限

    # 回声壁(随机捕获用户富有情感的句子,随机延时回显)
    echo_capture_rate: float = 0.1        # 符合「富有情感」的消息被采样的概率(其余不捕获)
    echo_min_chars: int = 6               # 少于该长度的消息不进入候选
    echo_max_chars: int = 120             # 超长消息截取最富情感的一句,长度上限
    echo_reveal_min_hours: int = 24       # 回显最小延迟(小时)
    echo_reveal_max_hours: int = 168      # 回显最大延迟(小时)
    echo_push_interval_hours: int = 12    # 推送节流:每半天最多回显一条(24=每天一条),多次到期的回声按此间隔依次放出
    echo_max_pending: int = 5             # 每用户待回显队列上限,超出则放弃新捕获(避免回显成灾)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime():
    s=get_settings()
    if s.environment not in ("development","test","production"):
        raise RuntimeError("invalid ENVIRONMENT")
    if s.environment=="production":
        if any((s.mock_llm,s.mock_wechat,s.mock_speech,s.mock_vision,s.mock_storage)):
            raise RuntimeError("Production forbids mock gateways")
        required=(s.llm_api_key,s.wechat_appid,s.wechat_secret,s.maas_api_key,
                  s.cos_secret_id,s.cos_secret_key,s.cos_bucket)
        if not all(required): raise RuntimeError("Production requires LLM/WeChat/speech/private storage configuration")
        if not s.public_base_url.startswith("https://") or "*" in s.cors_origins:
            raise RuntimeError("Production requires HTTPS and explicit CORS origins")
    if s.mock_storage and len(s.media_signing_key)<32:
        raise RuntimeError("Local media requires MEDIA_SIGNING_KEY (at least 32 characters)")
