检查时间：2026-09-14 21:39–21:45（北京时间）。代码版本：main，feea796「功能合格，加入ocr」。检查开始时工作区干净；本次仅增加此诊断文档，没有修改业务代码或重启服务。

结论：当前“等待中”来自单工作线程队列被前序模型请求占用；此前“准备媒体”长期不动，包含一个已经复现的状态文件命名错误。真实长耗时发生在向模型服务发送带大量图片的请求及其重试过程中。该视频已经成功取得平台字幕，现有证据不支持把本次问题归因于 OCR 或 Whisper 运算。

| 检查项 | 实际结果 |
| --- | --- |
| 后端 | PID 110972，127.0.0.1:8483，任务状态 API 正常返回 |
| 前端 | Node/Vite 正常监听 3015 |
| 执行并发 | 进程环境 TASK_MAX_WORKERS=1，来自 backend/start-gpu.sh:21 |
| 最新任务 | c0ff2d03-bb0c-4dc5-9e1d-376250ad4faa，21:29 写入 PENDING，只有状态文件 |
| 当前前序任务 | 8d7ae3fa-7724-47c1-8e50-8bd2dc88afa2，属于 BV1JMbw62EWj，主状态 DOWNLOADING，markdown 子状态 SUMMARIZING |
| GPU | RTX 5060 Laptop，检查瞬间利用率 0%，显存 677/8151 MiB |
| 磁盘 | 剩余约 897 GiB |

该视频的执行证据来自 backend/logs/app.log:1167 及 backend/note_results：

- 视频 BV19mYE6ZEM8，标题「全网最细九五讲解！四套九五一个视频全部吃透！学会成为九五高手！爽玩九五轻松吃鸡！」，约 692.837 秒。
- 18:33:09 解析 cid；18:33:10 平台 ai-zh 字幕获取成功，共 413 段。
- 18:33:10 开始抽帧；18:33:31 进入图像编码；同分钟已落盘完整 transcript 缓存。
- 任务 6b702424-8caa-4b83-8204-5838f7d9f1cf 于 19:19:54 失败，主状态记录 Connection error.，模型 checkpoint 的 partials 为空。约 46 分钟没有得到第一份总结。
- 后续另一视频任务 9296521e-62bf-4384-b6e2-f1a416dc8291 也以 Connection error. 失败，耗时约 73 分钟；20:32 再次启动同视频的新任务，仍占用唯一执行位。

具体问题：

1. backend/app/services/note.py:506 用 markdown_cache_file.stem 当 task_id，得到的是 UUID_markdown。SUMMARIZING 写入 UUID_markdown.status.json，而轮询接口读取 UUID.status.json，原文件仍为 DOWNLOADING。本地直接执行真实 _summarize_text、用假模型捕获执行中的状态，再调用真实 get_task_status，成功复现这个错误。checkpoint_key 可以保留 UUID_markdown，但状态必须使用原任务 ID。
2. backend/app/utils/openai_client.py:42 将代理客户端超时设为 600 秒，未关闭 SDK 自动重试。安装版本 openai 1.70.0 默认 max_retries=2；backend/app/gpt/universal_gpt.py:217 又执行最多 3 次外层尝试。使用 httpx.MockTransport 模拟 500 响应、完全不访问网络，观测到共 9 次请求。超时是网络阶段等待阈值，并不是整个任务的固定总时限；不应把它简单视为“最多十分钟”。
3. 视频理解按每 2 秒、2×2 拼图生成大量图片。该视频缓存共 86 张 1920×1080 拼图，JPEG 共 23.48 MiB，Base64 共 31.31 MiB；低于当前 45 MiB 分块阈值。当前占用队列的另一个视频有 131 张拼图，Base64 共 48.39 MiB，需要拆分。图片会随模型请求发送，即使平台字幕已经可用。
4. 后端当前连接 127.0.0.1:7890。两次宿主机检查可见约 1.0–1.3 MB 未发送队列；第二次 TCP 统计显示 rwnd_limited=99.9%，表明该连接绝大多数活跃时间受对端接收窗口限制。数据仍有推进，不是完全无活动；证据将瓶颈定位到此模型上传连接，但不能单独断定是本地代理、代理上游还是服务端限制。
5. 任务队列仅在进程内，状态文件保留在磁盘。18:24 后端重启前留下的 17:54 DOWNLOADING 和 18:00–18:06 的四个 PENDING 不会自动恢复执行。启动没有中断任务恢复流程，查询未知任务也默认返回 PENDING，因此旧任务可以无限显示进行中。
6. NoteGenerator 使用普通 logging.getLogger，而持久化日志使用另一套 get_logger；部分关键流程信息没有进入 app.log。模型重试循环也没有逐次重试日志，排障可观测性不足。

已执行测试：

| 真实小请求 | 结果 | 耗时 |
| --- | --- | --- |
| 当前 qwen-plus，纯文本，直连 | HTTP 200，回答 OK | 0.97 秒 |
| 当前 qwen-plus，纯文本，代理 | HTTP 200，回答 OK | 4.66 秒 |
| 当前 qwen-plus，32×32 红色测试图，直连 | HTTP 200，回答“无法查看或分析图片……” | 1.45 秒 |
| 当前 qwen-plus，同一测试图，代理 | HTTP 200，同样未识别图片 | 2.64 秒 |

这些请求使用已有 Qwen 配置，不输出密钥。小请求证明接口不是全面不可用，也没有复现鉴权或额度错误。图像测试说明当前端点与 qwen-plus 的组合没有在本次测试中提供有效视觉识别；HTTP 200 不能证明视频理解成功，亦不据此推断所有同名模型或其他部署的能力。

现有测试命令：

```bash
cd backend
/home/unbekannter/miniconda3/envs/bilinote/bin/python -m pytest \
  tests/test_task_serial_executor.py \
  tests/test_text_extraction_routes.py \
  tests/test_request_chunker.py \
  tests/test_video_reader_dedupe.py -q
```

结果：20 passed in 1.32s。上述状态文件复现和 9 次请求复现为额外临时诊断测试；现有 20 项通过不代表已覆盖这些缺陷。

建议处理顺序：

1. 为尽快拿到该视频的文字总结，关闭视频理解，复用已取得的平台字幕。选择 OCR 不会强制 OCR：现有流程优先平台字幕，此视频无需 OCR/Whisper。该路径尚未完成整段字幕端到端生成验证。
2. 恢复执行位时，需要处理当前在途任务及重启后遗留状态。只刷新页面或继续新建任务不能释放后端工作线程；仅重启后端也不会自动恢复旧任务，应同步将中断任务标记为可重试。
3. 修正总结阶段任务 ID；配置分开的 connect/write/read 超时，仅保留一层重试，并给出阶段、重试次数和耗时日志。
4. 增加排队原因、队列位置、取消和启动时中断恢复。不要仅靠提高 GPU 任务并发数解决网络阻塞。
5. 确需画面理解时，先验证实际端点的视觉能力，再限制图片数量、分辨率及每个请求的体积。不能仅依靠 45 MiB 的字节分块阈值判断模型是否能处理输入。

验证限制：计划使用完整字幕及约 31 MiB 图片做直连/代理复现，以及整段纯字幕总结；该命令在执行前被自动审批拒绝，理由是需要明确授权将完整内容发送到外部 Qwen 服务。这些完整请求没有执行，因此没有声称它们成功，也没有生成视频总结。核心状态错误、重试叠加和实时队列/网络状态已有独立证据。
