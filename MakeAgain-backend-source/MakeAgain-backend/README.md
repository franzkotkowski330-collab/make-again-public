# Make Again backend · B07 candidate

本目录是 B07 实际后端候选源码。未由此工作任务部署生产。用户界面/真机验收、商户与模板配置、上线与回滚由主线程负责。

## 运行条件

- Python 3.12；SQLite 文件库（当前迁移器明确只支持 SQLite）。不要把 DATABASE_URL 指向来源库或生产库做测试。
- 安装 `requirements.txt`。`requirements-tested.txt` 记录本机验过的完整环境；Linux 应按主线程验证的候选环境安装，不直接升级正在服务的虚拟环境。
- **FFmpeg 必须在 API 和 worker 的 PATH 中**，用于真正解码 MP3。可用系统安装或经主线程核实的私有可执行文件；不是单靠 Python requirements 即可满足。
- 外部模型、微信、COS 均只能显式选择模拟；缺凭据不会自动生成假回复或假身份。正式 `ENVIRONMENT=production` 禁止任意 MOCK_* 开关。
- API 与持续 worker 必须使用同一个明确的候选 DATABASE_URL 和存储配置。SQLite API 先使用单个 uvicorn worker；多个后台消费者通过持久任务领取互斥，但例子按一个消费者部署。

## 安全的本地隔离运行

在本目录运行（DATABASE_URL 与 STORAGE_LOCAL_DIR 改成自己专用的绝对路径）：

```sh
export DATABASE_URL=sqlite:////absolute/path/to/isolated/b07.sqlite3
export STORAGE_LOCAL_DIR=/absolute/path/to/isolated/uploads
export ENVIRONMENT=test
export MOCK_LLM=true MOCK_WECHAT=true MOCK_SPEECH=true MOCK_VISION=true MOCK_STORAGE=true
export MEDIA_SIGNING_KEY='<生成至少32字符的独立测试签名密钥>'
export PUBLIC_BASE_URL=http://127.0.0.1:8000
export CORS_ORIGINS=http://127.0.0.1:3000
python preflight.py
python admin_cli.py migrate
uvicorn main:app --host 127.0.0.1 --port 8000
```

另开进程、使用相同环境启动消费者：

```sh
python admin_cli.py worker --forever --poll-seconds 3
```

`worker --max-jobs 100` 只用于有限批处理；不带 `--forever` 会在队列排空后退出。消费者支持 SIGTERM/SIGINT 正常停止，每分钟查询原有未决订单，确认过期未付后调用原单关闭接口。不会创建新订单。`deploy/make-again-worker.service.example` 提供独立系统服务示例，需主线程调整路径与运行账号后启用；本任务没有启用系统服务。

停止旧消费者后运行 `python admin_cli.py recover-stopped-worker`：超过 15 分钟未更新的 memory/report/weekly/summary 作业转为 retry，message 作业转为 unknown（发送结果可能已成功，不能盲目重发）。正常错误的安全重试最多 3 次，有指数退避。可通过管理员 GET `/api/admin/jobs` 查看状态与错误码。

用户写请求使用 10 分钟处理期限；超期 `GET /api/operations/{request_id}` 或同请求重放会恢复状态。业务行与成功回执在同一事务提交；无成功回执的本地操作恢复为确定中断。支付操作指向已持久化的原订单，并要求查询原单；不重放外部扣款。迟到的原处理进程在提交前被状态校验拦截。

## 管理员与账号

```sh
python admin_cli.py init-admin --username '<独立管理员账号>'
```

密码只在终端交互输入，至少 12 字符，不打印、不作为命令参数，不复用服务器 root 密码。初始化不提升已有普通账号权限。使用 `/api/auth/login` 取得会话后可访问 `/api/admin/users`、`/api/admin/activation/*`、`/api/admin/audit` 等 API。当前没有新增管理 UI。

所有个人端点验证 Bearer、账号启停、路径和正文 user_id；资源 ID 与 companion_id 都核对归属。停用账号撤销全部会话；单独撤销接口 `/api/admin/users/{id}/revoke-sessions`。管理员读写记无私人正文的审计记录。公开注册不接受权限提升。

## 业务契约

完整端点见运行后的 `/openapi.json` 与工作记录 `修订记录/B07/backend/接口方案.md`。

- 保留原字段，新增 `request_id/client_request_id`、操作状态与回执。两个 ID 同时传且不同为 422；同 ID 不同操作/载荷为 409。同账号同 ID 原成功响应固定可查。
- `/state` 返回稳定 chat_session_id、companion_revision、interview 与 `capabilities.diary_update=true`。聊天最多 2000 字，消息不因退出页面或重启丢失。工具只给待确认建议；POST 确认后才保存。
- 日记 POST 同账号/陪伴/当地日期仅一条当前记录；PATCH 用 `expected_revision` 更新原 ID，原样保留空格换行。历史重复条目继续保留，通过 canonical_diary_id/is_current 标记。entry_date 按 IANA 时区校验，省略兼容 Asia/Shanghai；不接受未来日记或通过 PATCH 改日期。
- 归档需 companion_id + expected_revision；新陪伴要求原当前陪伴已归档。旧记录不删除，列表按陪伴过滤。树洞给其他人的回信是账号级社区活动，可在结束陪伴后按原规则进行，不修改归档记录。
- 照片与物品回执包含实际记录、陪伴、请求 ID 与短期图片 URL；列表能刷新过期 URL。物品抠图失败标记 `original_fallback` 并保留原图；不冒充已抠图。WAV 结构/参数/数据长度及 MP3 解码均实际校验。
- 新树洞信进入 pending_review，管理员确认后才 published；匹配只取已发布信。支持举报/屏蔽 API。前端若尚未接入举报入口，主线程仍需补用户可达路径后才开放真人社区。运营回信标记 operator，用户回信标记 user。

## 迁移与数据保护

`python admin_cli.py migrate` 或 API lifespan 执行可重复迁移：补列 → 旧 user_state 生成 companion → 按已有时间/归属回填 → 标记历史同日日记当前条目 → 版本标记。旧永久会员保留为 legacy，不能迁移时取消权益。

仅替换旧 user/date 唯一约束的少数表结构，事务内复制所有列/行；不删重复日记、不改原文。历史无法唯一归属到陪伴的记录保留 NULL，不猜入新陪伴；主线程部署前应仅统计此类记录数量，制定归属核对方案。测试夹具 `tests/fixtures/legacy-schema.sql` 只有旧表结构，完全不含生产行、正文或凭据。

迁移前仍需一致性备份和停写窗口；重复可执行不代表已完成生产恢复/回滚演练。不能直接让旧程序写入新版本库；回滚应使用主线程批准的备份/恢复方案。

## 正式支付配置（当前无默认商品）

需提供真实 `WECHAT_PAY_MCHID`、`WECHAT_PAY_SERIAL_NO`、`WECHAT_PAY_PRIVATE_KEY_PATH`、`WECHAT_PAY_PLATFORM_KEY_PATH`、`WECHAT_PAY_PLATFORM_SERIAL`、`WECHAT_PAY_API_V3_KEY`、`WECHAT_PAY_NOTIFY_URL`、`PAYMENT_ALLOWED_PLATFORMS`。平台密钥/证书需随微信轮换更新，序列号必须匹配。价格、期限、利益说明、条款和允许平台通过管理员 `/api/admin/subscriptions/plans` 设置，默认没有可售套餐。

仅实现已获准场景的普通 JSAPI 微信支付；未知/未获准平台不可售，不声称实现自动续费或其他支付通道。商户 API 请求和小程序参数 RSA 签名；供应商响应及回调验签；AES-256-GCM 解密；核对 appid/mchid/原单/用户/金额/CNY/成功状态；支付流水与会员期限只提交一次。支付通知入口 `/api/payments/wechat/notify`。

订单先在本地持久化 out_trade_no，再访问微信；未知下单结果保留 processing，通过 `GET /subscriptions/orders/by-request/{id}`、原单 resume/cancel 和 worker 查询恢复。延迟回调及陈旧查单不能降低 paid 终态。正式退款规则须由业务提供；本轮未实现退款执行入口，不把商品条款当作退款处理能力。

## 正式提醒配置

`WECHAT_SUBSCRIBE_TEMPLATES` 为 JSON 数组；每项需真实 `id,title,description,purpose,page,data`，可附 miniprogram_state。`data` 为审核通过用途的固定、非私人模板字段；发送不从日记或聊天自动取锁屏正文。空配置返回 messages_available=false。

用户 accept/reject/ban/filter 回执幂等保存；一次 accept 最多预留一条任务，不显示永久开启。管理员只能针对匹配用途排程，不能自行增加授权。拒绝/禁用/过滤清除可用额度并取消等待发送的任务。发送前再核对授权、账号和模板。微信明确可重试错误才安全重试；发送超时保留 unknown，须按运营处理规则核对，不能自动再发。

## 验证

```sh
python -m pytest tests -q
python -m compileall -q api memory agent gateway auth.py config.py main.py admin_cli.py preflight.py
python -m pip check
```

测试强制独立临时 SQLite、显式模拟外部网关；所有价格、身份、模板与密钥均为测试夹具。MP3 测试要求 FFmpeg，缺少时会跳过，不能计作完整通过。测试不覆盖微信真机、用户体验验收、正式商户或实际消息送达。

## B07 候选02：可选分页与社区操作

- `/photo/{user_id}`、`/item/{user_id}`、`/diary/{user_id}`、`/weekly-report/{user_id}`、`/reports/{user_id}`、`/treehole/letters/{user_id}` 可传 `limit=1..100` 与 `cursor`。不传二者时保留旧数组响应；分页额外返回 `next_cursor`（结束时 null）、`has_more`、`limit`、`companion_id`。档案每页显式传原 companion_id。
- 数据库按创建时间和 ID 做稳定 keyset 查询；首屏创建时间上限把后来新增记录留到刷新。cursor绑定账号/陪伴/集合/筛选且有效1小时；跨scope/过期需要刷新，不能复用别的列表cursor。
- `/todo/{user_id}` 分页再传 `bucket=queue|history`。queue按创建时间/ID升序，history按完成时间/ID降序；响应仅填所选桶，另一数组为空。小事新增/完成导致列表变化时409 `CURSOR_STALE`，从第一页刷新；不传分页仍返回原全部queue和最近4件history。
- 分页签名优先用**稳定、独立的 MEDIA_SIGNING_KEY（建议至少32字符）**；COS正式配置未单设时，从已有 WECHAT_SECRET 按专用用途派生，重启不变。没有任意进程随机密钥。两者均空会明确拒绝分页；正式模式本已要求真实WECHAT_SECRET。更换密钥后旧cursor失效，前端刷新列表即可。
- `POST /treehole/report` 传 **letter_id 或 reply_id，必须二选一**，reason为1..256字符自由文本（非空白），可附 `block_author:true`。收到回信应传reply_id，不能用自己所写原信ID代替。`POST /treehole/block` 同样二选一，不需reason，返回blocked=true。两者支持request_id幂等。
- 用户只能操作已发布的他人原信或已送达自己的回信；控制端点为账号级，可在阅读已归档信件时使用。服务端解析作者身份，响应不含作者账号。屏蔽后过滤匹配原信、回信弹窗与信件下的回信，数据保留供审核。
- 写信门槛沿用 `TREEHOLE_WRITE_SESSIONS` 配置名，改为实际完成的成对对话轮数。稳定会话ID保持不变，重复请求与失败消息不计新轮数；资格提供completed_turns/min_turns，旧chat_sessions仍是真实distinct数。
- 增加 `community_reports.reply_id` 可空列，旧举报行和ID保留；同一迁移入口可重复运行。
