# 多用户任务调度

默认适用于单机、单后端调度器部署。启动方式保持 `python main.py` 或 `bash start-gpu.sh`；两者任务并发默认均为 6。启动会创建任务执行表，旧 SQLite 安装首次迁移前自动备份。部署新版本需先停止旧后端，再启动；不要混跑旧的内存队列版本。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| TASK_MAX_WORKERS | 6 | 全站已领取执行的工作流；包括等待阶段资源的任务 |
| TASK_USER_ACTIVE | 1 | 每用户已领取执行的工作流 |
| TASK_USER_QUEUE_LIMIT | 3 | 每用户尚未领取的任务 |
| TASK_QUEUE_LIMIT | 20 | 全站尚未领取的任务 |
| TASK_GPU_SLOTS | 1 | Whisper 与 CUDA OCR 共用；当前单 GPU 推理服务请保持 1 |
| TASK_CPU_SLOTS | 2 | CPU 密集阶段，包括抽帧、转码、OCR、转写及索引 |
| TASK_API_SLOTS | 2 | 同时在途的总结请求，包括分段、合并与重试 |
| TASK_TIMEOUT_SECONDS | 7200 | 每次工作流进程的最长生存时间，含等待资源的时间 |
| TASK_INFERENCE_TIMEOUT_SECONDS | 1800 | 单次本地推理请求超时 |
| TASK_HEARTBEAT_TIMEOUT_SECONDS | 90 | 任务心跳超时；每 5 秒更新一次心跳 |
| TASK_RETRY_ATTEMPTS | 2 | 工作流临时网络失败的总尝试次数 |
| WHISPER_CPU_THREADS | 2 | Whisper 内部 CPU 线程 |
| WHISPER_KEEP_DURING_OCR | 0 | 可选模型共驻留；设为 1 时，仅在预留显存足够时保留 Whisper |
| OCR_GPU_RESERVE_MIB | 3072 | 开启共驻留时，OCR 开始前要求的剩余显存 MiB；无法测量则卸载 Whisper |
| VIDEO_FRAME_WORKERS | 2 | 单次抽帧内部并发数；FFmpeg 单进程线程限制为 1 |
| EMBEDDING_CPU_THREADS | 2 | 向量索引/问答 embedding 的 ONNX CPU 线程，固定 CPU provider |
| OCR_THREADS | 1 | ONNX Runtime 内部线程数 |
| TASK_RUNTIME_DIR | data/task_runtime | 单机共享的调度器锁、资源锁和推理 socket 目录 |

## 调度与接口

任务提交到数据库，由唯一调度器按用户 ID 轮流选择用户，同一用户内部按提交时间处理。已有活跃任务的用户跳过。调度不会抢占正在执行的长任务。队列已满返回 HTTP 429 和 Retry-After；拒绝提交不会留下空的历史任务。

`POST /api/generate_note` 可携带 `request_id`（1～80 字符），同一账号、同一提交编号重复请求只返回原 task_id。相同编号配不同参数返回 409。客户端连接超时重发时必须沿用编号。未提供编号的旧客户端仍可工作，但新建任务无法保证跨请求幂等。已完成/取消任务重新提交时用新 request_id；通过原 task_id 重试可复用缓存。排队或执行期间禁止修改参数。

`POST /api/cancel_task` 的请求体为 `{"task_id":"..."}`。仅允许取消自己尚未领取的任务；已经开始或结束返回 409。取消与领取在同一事务规则下互斥。任务状态接口和历史列表返回 `can_cancel`；WAITING_RESOURCE 和 WAITING_RETRY 仍是执行中的工作流，不能取消。取消后可重试或删除。删除尚在排队/执行的任务会返回 409。

`GET /api/task_queue` 返回当前用户的执行/排队数及配置；管理员还可看到全站状态计数和最早排队时长。任务阶段计时保存在 task_jobs.stage_times，详细错误保存在 error_type/error_detail，日志中的资源阶段包含等待和运行耗时。工作流进程和模型进程的日志继承后端日志输出。

## 资源与显存

一个调度器管理独立任务进程，另有一个本地推理进程持有 Whisper 模型。任务间使用本机文件锁共享资源额度，不给客户固定分配 GPU 百分比。需要 CPU 和 GPU 的步骤同时取得两种资源，不会持有一种资源等待另一种。网络下载本身不占 CPU 名额，yt-dlp 原生后处理、解码校验、转码、抽帧及本地模型处理才占用。

同配置 Whisper 可复用；切换配置先显式卸载旧模型。CUDA OCR 启动前先卸载 Whisper，OCR 在独立子进程完成后释放显存，后续 ASR 按需重新加载。默认不启用两套模型共同驻留。普通进程退出、异常、超时都会回收资源；本地推理进程异常退出时先回收旧模型进程，再启动新进程；调用方保留 CPU/GPU 额度并有限重连，不中断无关的下载或付费总结请求。

当前单卡实测允许选择 `WHISPER_KEEP_DURING_OCR=1`，减少 ASR/OCR 交替时的重复加载。该选项只保留权重，GPU 计算仍共用一个名额；它检查的是 OCR 启动前的剩余显存，不构成任意输入下的显存保证。更换模型、素材或显卡后需重新验证。向量索引与问答 embedding 显式使用 CPU provider，并限制 ONNX 内部线程，避免绕过 GPU 名额占用显存。

总结的每次实际 API 请求占用名额，退避等待时释放 API 名额。并发额度不能代替供应商 RPM/TPM 配额：供应商 429/临时错误按有限重试处理，额度不足等永久错误停止。同一接口地址与 API Key 的限流冷却时间跨任务共享；尊重 Retry-After 的秒数或 HTTP 日期，冷却等待期间不占 API 名额。UniversalGPT 自带 OPENAI_RETRY_ATTEMPTS 次请求尝试（默认 2），工作流可再尝试 TASK_RETRY_ATTEMPTS 次；最坏情况下同一失败请求尝试次数为两者乘积。API 响应丢失后重试仍可能重复计费。

## 恢复与边界

执行参数和转写配置在提交时保存。完成的媒体、字幕、带输入签名的总结及分段检查点会复用。中断中的阶段可能重做，不能保证逐帧恢复。总结缓存签名包含模型、接口地址、文字及生成参数。失败后的重试复用有效总结；对已成功任务明确重新生成则换发总结版本标识，保留重新调用模型生成新版本的行为。

正常停机先停止调度、回收所有任务及推理进程，然后释放单实例锁。Linux 下子进程持续检测父进程，后端异常退出时杀掉自己的进程组；任务及推理进程继承单实例锁，旧进程退出前新调度器无法取得锁。启动时对旧执行尝试换发令牌，防止旧进程写入新状态；恢复中的工作流保留原执行名额，满队列重启时不会把排队数从 20 扩到 23。

运行中任务进程异常退出会自动从缓存重试，最多恢复 2 次；心跳/总时长超时停止并提示用户重试。服务重启最多自动恢复同一任务 3 次，防止坏任务无限循环。用户手动重试重新计数。

本实现使用单机文件锁和本地 IPC，不能直接跨机器扩容。不要启动多个 Uvicorn/Gunicorn worker，也不要为同一数据库配置不同运行目录。当前主要验证 Linux 部署；其他平台进程树回收需要单独验证。容器内存上限和磁盘空间仍需根据模型与视频实测配置。

## 验证

在 backend 目录执行：

```bash
python -m pytest tests/test_task_scheduling.py -q
```

自动测试使用临时数据库、模拟下载器/模型接口及独立进程，不访问付费 API、不下载模型。覆盖并发提交、队列上限、公平性、幂等、取消竞争、跨进程资源限制、模型复用/卸载、重启恢复和总结缓存复用。本机模拟多用户与真实 GPU 压测结果见 [CONCURRENCY_BENCHMARK.md](CONCURRENCY_BENCHMARK.md)。

### 初始验证记录（2026-10-16）

- 调度与恢复测试 20 项全部通过，包含旧数据库升级备份、接口权限、失败重试缓存复用及成功笔记重新生成。
- 前端生产构建通过。全量 TypeScript 检查仍有仓库既有的 72 项诊断；与修改前对比，无新增诊断。
- 在 RTX 5060 Laptop GPU（8151 MiB）上，用本地 large-v3 / int8_float32 和 CUDA RapidOCR 连续完成两轮 Whisper→OCR 切换。合成音频每次 2 秒，同配置 Whisper 重复调用后再卸载，OCR 用合成文字图片验证。
- 两轮 Whisper 调用结束时显存均约 1725 MiB，卸载后均约 115 MiB；OCR 子进程退出后仍为 115 MiB。整个验证的采样峰值为 2237 MiB，推理服务关闭后恢复到 12 MiB。
- 原始记录：[data/task_validation/report.json](data/task_validation/report.json)。这验证了短输入下的推理 IPC、模型复用、显存切换和回收，不代表长视频吞吐、识别质量或持续压测结果。

复现本地 GPU 验证（仅使用已有模型，不下载、不调用付费 API）：

```bash
BILINOTE_PYTHON=/home/unbekannter/miniconda3/envs/bilinote/bin/python bash start-gpu.sh scripts/verify_task_resources.py --model large-v3 --cycles 2
```

### 模拟多用户容量验证（2026-10-16）

后续实测完成 10 组隔离工作流，132/132 成功且建立索引，并完成 800 次突发提交检查。短素材与更长的横竖屏混合素材均显示：全站工作流从 6 加到 12 未提高总体吞吐；单独 Whisper 实验的 GPU 并发 1 快于 2/3/4。按用户确认，已将工作流默认值由 3 调整为 `TASK_MAX_WORKERS=6`，GPU/CPU/API 名额保持 1/2/2，可选模型共驻留保持关闭；已运行的后端需重启后读取新值。

发现并修复认证/历史查询嵌套占用连接池的问题，固定 embedding 使用 CPU，并增加默认关闭的显存缓存选项。调度回归测试现为 **24 项全部通过**；相关 Python 编译检查、`git diff --check` 通过。完整方法、适用边界、数值和复现命令见 [并发压测报告](CONCURRENCY_BENCHMARK.md)。总结接口为模拟延迟，未验证供应商配额或公网带宽。
