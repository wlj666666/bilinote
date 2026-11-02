<div style="display: flex; justify-content: center; align-items: center; gap: 10px;
">
    <p align="center">
  <img src="./BillNote_frontend/src/assets/icon.svg" alt="BiliNote Banner" width="50" height="50" />
</p>
<h1 align="center">BiliNote</h1>
</div>

<p align="center"><i>AI 视频笔记生成工具 —— 让 AI 为你的视频做笔记</i></p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" />
  <img src="https://img.shields.io/badge/frontend-react%2019-blue" />
  <img src="https://img.shields.io/badge/backend-fastapi-green" />
  <img src="https://img.shields.io/badge/LLM-openai%20%7C%20deepseek%20%7C%20qwen-ff69b4" />
  <img src="https://img.shields.io/badge/docker-ghcr.io-blue" />
</p>




## ✨ 项目简介

BiliNote 是一个 AI 视频笔记助手：给定一个视频链接，它会自动下载视频、提取音频、转写文字（或直接从画面里 OCR 出硬字幕），再交给大模型生成结构清晰、重点明确的 Markdown 笔记，并可插入视频截图与原片时间戳跳转。

支持哔哩哔哩、YouTube、抖音、TikTok、快手与本地视频文件。

### 本分支的主要改动

本仓库基于上游 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) 二次开发，核心改动集中在**多用户部署**与**本地推理能力**两块：

- **管理员 / 多用户登录与数据隔离**：模型供应商、转写器配置、系统监控等管理功能收敛到管理员；笔记、上传文件、聊天记录、向量索引按用户隔离。旧数据自动归入原管理员。详见 [管理员登录与凭据保护](understanding/管理员登录与凭据保护.md) 与 [用户数据隔离与旧历史迁移](understanding/用户数据隔离与旧历史迁移.md)。
- **硬字幕 OCR 提取**：视频没有可用字幕时，直接对画面做 OCR 提取硬字幕，支持 CPU / GPU 双路径，GPU 下实测约 6 倍加速。详见 [OCR 推理加速路线图](understanding/OCR_推理加速路线图.md)。
- **多用户任务调度与资源限额**：全站并发、单用户并发、排队长度、GPU / CPU / API 分阶段槽位统一由 `TaskLimits` 管控，任务可取消、可从中断处恢复。详见 [任务调度说明](backend/TASK_SCHEDULING.md)。

## 🔧 功能特性

### 内容生成

- 多平台支持：Bilibili、YouTube、抖音、TikTok、快手、本地视频文件
- 自动下载 → 提取音频 → 转写 → 大模型总结的完整流水线
- 笔记格式与风格可选，支持多版本保留与对比
- 可选插入视频截图（自动按时间戳截取）
- 可选原片时间戳跳转链接
- 笔记顶部视频封面 Banner
- 多模态视频理解（画面采样拼图后交给多模态模型）

### 转写与字幕

- 本地转写：Fast-Whisper、MLX-Whisper（仅 Apple Silicon）
- 在线转写：Groq、必剪（BCut）、快手
- YouTube / Bilibili 优先复用平台字幕，命中时跳过音频下载
- 硬字幕 OCR：RapidOCR，支持 CPU / CUDA 双路径

### 模型与配置

- 自行配置任意 OpenAI 兼容供应商（OpenAI、DeepSeek、Qwen 等）
- 全局代理：一处配置同时作用于模型接口、转写接口与视频下载，支持 `HTTP_PROXY` 兜底
- 转写模型就绪门禁：本地模型未下载完成时拦截任务并引导下载
- Whisper 模型损坏自愈：`model.bin` 截断时自动删除重下

### 问答与检索

- 基于 RAG 的笔记内容 AI 问答，支持 Function Calling
- 向量索引包含视频元信息（标题、作者、简介、标签）
- 半屏 / 全屏两种问答视图

### 多用户与运维

- 管理员 / 普通用户两级角色，密码以 PBKDF2-SHA256 存储
- 笔记、文件、聊天、向量索引按用户隔离
- 任务调度：全站并发、单用户并发、排队、分阶段资源槽位
- 系统监控页：CUDA 状态、FFmpeg、数据库、Whisper 模型就绪情况

## 🏗️ 技术架构

**后端**（Python 3.11 + FastAPI，端口 8483）

| 目录 | 职责 |
| --- | --- |
| `app/routers/` | HTTP 路由：`note`（生成）、`auth`（登录）、`provider` / `model`（供应商）、`config`、`chat`（RAG 问答）、`history`、`files` |
| `app/services/` | 业务逻辑：`note.py` 编排完整流水线；`task_scheduler.py` + `task_limits.py` 任务调度；`auth.py` 鉴权；`storage_scope.py` 任务级存储隔离；`vector_store.py` 向量检索 |
| `app/downloaders/` | 平台适配器：bilibili / youtube / douyin / kuaishou / local |
| `app/transcriber/` | 语音转写引擎，工厂在 `transcriber_provider.py` |
| `app/ocr/` | 硬字幕 OCR：RapidOCR 封装、字幕布局学习、时序合并、CUDA 运行时 |
| `app/gpt/` | 大模型集成，工厂模式 + 长文本分块 |
| `app/db/` | SQLite + SQLAlchemy，DAO 模式 |

**前端**（React 19 + Vite + TypeScript，开发端口 3015）

- `pages/HomePage/` —— 主工作区：输入表单、Markdown 预览、思维导图
- `pages/SettingPage/` —— 设置页：模型、转写器、下载配置、提示词、系统监控、用户管理
- `pages/Login/`、`pages/Onboarding/` —— 登录与首启引导
- `store/` —— Zustand（`taskStore` / `authStore` / `modelStore` / `providerStore` / `configStore` / `chatStore`）
- 设计语言：Tailwind v4 + Radix UI + antd / `@ant-design/x`，主色 sage 绿 `#6e7f64`，明暗双主题

**其它**

- `BillNote_extension/` —— 浏览器扩展（Vue 3 + MV3，Chrome / Edge / Firefox）
- `BillNote_frontend/src-tauri/` —— 桌面端打包（Tauri 2）

## 📸 截图预览

> 待补充：旧版截图（蓝色主题）已清理，需按当前 sage 绿主题重新截图后放回 `doc/` 并在此引用。

## 📦 桌面版下载

桌面客户端（Windows / macOS）可在 [Releases](https://github.com/wlj666666/bilinote/releases) 页面下载。

> Windows 用户请注意：请勿在含中文的路径下运行。

## 🚀 快速开始

### 方式一：Docker 部署（推荐）

确保已安装 Docker（含 Compose v2，即 `docker compose` 子命令），直接拉取预构建镜像运行：

```bash
docker pull ghcr.io/wlj666666/bilinote:latest

docker run -d -p 80:80 \
  -v bilinote-data:/app/backend/data \
  -v bilinote-config:/app/backend/config \
  -v bilinote-static:/app/backend/static \
  -v bilinote-uploads:/app/backend/uploads \
  -v bilinote-vector-db:/app/backend/vector_db \
  -v bilinote-models:/app/backend/models \
  --name bilinote \
  ghcr.io/wlj666666/bilinote:latest
```

六个卷分别持久化：

| 卷 | 容器内路径 | 内容 |
| --- | --- | --- |
| `bilinote-data` | `/app/backend/data` | SQLite 数据库（含 LLM 供应商配置、笔记历史）+ 生成的笔记 + 任务运行时缓存 |
| `bilinote-config` | `/app/backend/config` | 平台 Cookie、转写器运行时设置、任务超时覆盖 |
| `bilinote-static` | `/app/backend/static` | 笔记引用的视频截图 |
| `bilinote-uploads` | `/app/backend/uploads` | 用户上传的本地视频 |
| `bilinote-vector-db` | `/app/backend/vector_db` | RAG 问答的向量索引（**不挂会每次重建**） |
| `bilinote-models` | `/app/backend/models` | Whisper 模型缓存（可选，避免每次重新下载） |

这样 `docker pull` 升级新镜像、删旧容器重建后，配置和历史都不会丢。

> ⚠️ **不要**用 `-v 卷名:/app/backend` 挂整个后端目录——命名卷会用首次启动时的镜像内容固化，之后 `docker pull` 升级也会被旧代码盖住，导致「升级不生效」。只挂上面这些数据子目录即可。

#### 🔑 首次部署必须创建管理员账号

**这一步不能跳过。** 新版加入了登录与多用户隔离：模型供应商、转写器配置、系统监控等页面都要求**管理员**权限，而前端注册接口只能创建**普通用户**。管理员只能通过命令行创建：

```bash
# 单镜像（上面的 docker run 方式）
docker exec -it bilinote python scripts/manage_users.py init-admin admin

# docker compose 方式
docker compose exec backend python scripts/manage_users.py init-admin admin
```

命令会交互式要求输入密码（**12–128 字符**，输入时不回显，需输入两次）。

完成后访问 `http://localhost`，用该账号登录即可进入「设置」页配置模型供应商。

其它账号管理命令：

```bash
docker exec -it bilinote python scripts/manage_users.py create-user alice   # 创建普通用户
docker exec -it bilinote python scripts/manage_users.py reset-password admin # 重置密码
```

> 普通用户账号也可以由管理员在前端「用户管理」页创建，无需进容器。

访问：`http://localhost`

也可以使用 docker compose 本地构建：

```bash
cp .env.example .env            # 第一次部署务必先创建 .env
docker compose up --build -d
docker compose exec backend python scripts/manage_users.py init-admin admin

# GPU 加速部署（需要 NVIDIA GPU + NVIDIA Container Toolkit，详见下方「CUDA / GPU 加速」）
docker compose -f docker-compose.gpu.yml up --build -d
```

> 老版本 Docker 只有独立的 `docker-compose`（v1）命令，把上面的 `docker compose` 换成 `docker-compose` 即可，参数完全一致。

#### Docker 部署常见问题（FAQ）

社区反馈最集中的几个坑，遇到先按下面排查：

**0. 国内拉不到 docker.io（build 阶段报 `dial tcp ... i/o timeout`）**

`docker compose build` 拉 `python:3.11-slim` / `node:20-alpine` / `nginx:1.25-alpine` 时连 `auth.docker.io` 超时。三种解法，按推荐顺序：

- **方法 A：直接用预构建镜像（最省事）**——不要本地 build，跳到上面的 `docker pull ghcr.io/wlj666666/bilinote:latest` 路径，ghcr.io 在国内通常比 docker.io 顺。
- **方法 B：配置 Docker daemon 镜像加速器**——编辑 `/etc/docker/daemon.json`（Docker Desktop 在设置 → Docker Engine 里改），加：
  ```json
  {
    "registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1panel.live"]
  }
  ```
  然后重启 Docker Desktop / `sudo systemctl restart docker`。这是一劳永逸的做法。
- **方法 C：临时切换 base image 镜像源**——本项目所有 Dockerfile 都暴露了 `BASE_REGISTRY` build-arg：
  ```bash
  BASE_REGISTRY=docker.m.daocloud.io docker compose build
  docker compose up -d
  ```
  或永久写到 `.env`：`echo 'BASE_REGISTRY=docker.m.daocloud.io' >> .env`。

注意：Chinese 公共 docker 镜像源时常被关停，2025-2026 之间可用的列表会变；如果 `docker.m.daocloud.io` 不通，搜一下"Docker 镜像加速 可用"找最新可用源即可。

**1. 容器一直 restart / unhealthy**

先看后端日志：
```bash
docker logs -f bilinote-backend
```
后端启动会按顺序打印 `[startup 1/5] ... [startup 5/5] 启动完成`。若日志卡在某一步或出现 `[startup FAILED]`，就是那一步的问题，常见：
- **卡在 `[startup 3/5]`**：转写器配置读不到。检查 `.env` 里 `TRANSCRIBER_TYPE` 是否写错，`mlx-whisper` 只能在 Apple Silicon 用，Linux/Docker 请用 `fast-whisper` 或 `groq`。
- **首次跑视频时容器被 kill**：whisper 模型下载触发 OOM。先把 `.env` 里 `WHISPER_MODEL_SIZE` 改成 `tiny`，跑通后再去前端「音频转写配置」里逐档升。同时注意 `TASK_MAX_WORKERS`（默认 6）与 `mem_limit` 的匹配关系。

健康检查说明：compose 的 healthcheck 打的是公开端点 `/api/sys_check`（返回 200 即健康）。**不要**改成 `/api/sys_health`——后者需要管理员 Bearer token，容器内匿名 curl 会恒 401，导致容器永远 `unhealthy`、nginx 永远不启动。

**2. 改了 `.env` 没生效**

区分两类变量：
- `VITE_*` 是**构建时**变量（会被烘进前端 JS bundle）。不过 Docker 部署下前端固定走 nginx 的 `/api` 反代，**不需要设置 `VITE_API_BASE_URL`**；改了它也不会生效，除非重新 build 前端。
- 其他后端变量（`TRANSCRIBER_TYPE`、`WHISPER_MODEL_SIZE`、`FFMPEG_BIN_PATH`、`OCR_DEVICE`、`TASK_*` 等）是**运行时**变量，改完 `docker compose up -d` 即可。

注意：**LLM API key 不要写 `.env`**，从前端「模型供应商」页面录入，会保存到 SQLite 数据库并持久化。

**3. 数据存在哪？删容器会丢吗？**

`docker compose` 把宿主机的 `./backend` 绑挂到容器的 `/legacy`（不是 `/app`，`/app` 保持镜像内代码，避免升级被旧代码盖住）。以下内容都在宿主机 `./backend/` 下、删容器不会丢：

- `./backend/bili_note.db` —— SQLite 库（含账号、LLM 供应商配置、笔记历史）
- `./backend/config/` —— Cookie、转写器设置、任务超时覆盖
- `./backend/static/` —— 视频截图
- `./backend/uploads/` —— 上传的本地视频
- `./backend/vector_db/` —— RAG 向量索引
- `./backend/data/` —— 任务运行时缓存、源文件缓存、笔记输出
- `./backend/models/` —— Whisper 模型缓存

要彻底重置就 `docker compose down && rm -rf backend/bili_note.db backend/config backend/vector_db`。

> 单镜像（`docker run`）方式请对照上面六个命名卷的表格。

**4. 前端打开是空白页 / 报 502**

通常是 nginx 起来了但 backend 还没 healthy。`docker ps` 看 backend 容器 STATUS 是不是 `(healthy)`；若长期 `(unhealthy)`，按问题 1 排查后端日志。

**5. 不要用 `restart: on-failure:N`**

如果你 fork 后改过 compose 文件、把 restart 策略改成了 `on-failure:3`：任何 3 次连续崩溃都会让容器永远不再启动，之后改 `.env` 也没用。本项目自带的 compose 已经统一用 `unless-stopped`。

**6. 上传大文件失败 / 长任务返回 504**

nginx 默认 `proxy_read_timeout` 是 60 秒，长 LLM 调用会被掐断。本项目的 `nginx/default.conf` 与 `nginx/standalone.conf` 已配置 `proxy_read_timeout 300s` 和 `proxy_request_buffering off`（大文件直通后端，不先落盘）。若你自建反代，请同步这两项。

**7. 多人同时登录时第 11 个人报 429**

后端登录限流按客户端 IP 计数（10 次/分钟）。若反代没透传真实 IP，所有用户会被算成同一个来源。本项目 nginx 已设置 `X-Forwarded-For`，compose 里也设了 `FORWARDED_ALLOW_IPS=*`（仅 Docker 内网可达）。若你把后端直接暴露到公网，请把 `FORWARDED_ALLOW_IPS` 置空，否则外部可伪造该头绕过限流。

### 方式二：源码部署

#### 1. 克隆仓库

```bash
git clone https://github.com/wlj666666/bilinote.git
cd bilinote
mv .env.example .env
```

#### 2. 启动后端（FastAPI）

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # 硬字幕 OCR 依赖；不做 OCR 可跳过
python main.py
```

> 想用 GPU 加速（转写 + OCR），Linux/WSL 上额外装：
> ```bash
> pip install -r requirements-gpu.txt      # faster-whisper 的 CUDA 运行时
> pip uninstall -y onnxruntime
> pip install -r requirements-ocr-gpu.txt  # OCR 的 CUDA 运行时
> ```
> 然后用 `./start-gpu.sh` 启动（会自动注入 `LD_LIBRARY_PATH` 并设 `OCR_DEVICE=cuda`），不要直接 `python main.py`。

首次启动后同样需要创建管理员账号（模型供应商等页面要求管理员权限）：

```bash
python scripts/manage_users.py init-admin admin
```

#### 3. 启动前端（Vite + React）

```bash
cd BillNote_frontend
pnpm install
pnpm dev
```

访问：`http://localhost:3015`

## ⚙️ 依赖说明

### 🎬 FFmpeg
本项目依赖 ffmpeg 用于音频处理与转码，源码部署时必须安装：
```bash
# Mac (brew)
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows
# 请从官网下载安装：https://ffmpeg.org/download.html
```
> ⚠️ 若系统无法识别 ffmpeg，请将其加入系统环境变量 PATH
>
> Docker 部署已内置 FFmpeg，无需额外安装。

### 🚀 CUDA / GPU 加速（可选）

本地 **Faster Whisper** 转写与**硬字幕 OCR** 可用 NVIDIA GPU 加速（在线引擎 Groq / 必剪 / 快手 与 GPU 无关）。仓库已自带 GPU 镜像与编排，**无需改代码**。

**1. 宿主机前提**

- NVIDIA 显卡 + 较新驱动（需支持 CUDA 12.x），宿主机 `nvidia-smi` 能正常输出；
- 安装 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)（最易漏的一步，没它 Docker 进不去 GPU）。装完验证：
  ```bash
  # 用一个任意带 CUDA 的镜像测透传；也可以直接用本项目的 GPU 镜像
  docker run --rm --gpus all python:3.11-slim nvidia-smi
  ```
  能列出显卡即 OK。

**2. 切换到 GPU 编排**（在源码目录里）

CPU 与 GPU 两套 compose 用了相同的容器名，先停掉当前栈再起 GPU 栈：

```bash
docker compose down                                       # 停掉当前（CPU）栈
docker compose -f docker-compose.gpu.yml up --build -d    # 用 GPU 栈重建
```

- GPU 栈用 `backend/Dockerfile.gpu`：基础镜像与 CPU 栈**完全相同**（`python:3.11-slim`），因此 Python 版本（3.11）与 CPU 镜像、CI 保持一致，不会出现「只在 GPU 路径上踩版本差异」的问题。
- CUDA 运行时（cuBLAS / cuDNN / cuFFT / cuRAND / cuda_runtime）由 `requirements-gpu.txt` / `requirements-ocr-gpu.txt` 里的 pip wheel 提供，**不需要 `nvidia/cuda` 基础镜像**；运行时真正需要的驱动 `libcuda.so` 由 NVIDIA Container Toolkit 注入。
- 这些 wheel 把 `.so` 装在 `site-packages/nvidia/*/lib`，该路径不在动态链接器搜索范围内。镜像构建时会自动把这些目录写进 `/etc/ld.so.conf.d/` 并执行 `ldconfig`——**少了这一步，设置页会显示「CUDA 不可用」**，尽管显卡完全正常。
- **数据不丢**：两套 compose 都绑挂 `./backend`，数据库 / 配置 / 已下载模型都保留。
- 首次构建较大较慢（要下载 `onnxruntime-gpu` 及若干 nvidia CUDA wheel，约 2–3 GB），最终镜像约 8 GB，耐心等。

**3. 启用并确认**

- 「设置 → 音频转写配置」转写引擎选 **Faster Whisper（本地）**，GPU 下可放心选大模型（如 `large-v3`）。
- 硬字幕 OCR 默认 `OCR_DEVICE=cuda`（GPU compose 已设）；设为 `cuda` 但缺少 GPU 依赖时会**直接报错，不会静默回退 CPU**。
- 确认真的走了 GPU，三种方式任选：
  ```bash
  # a) 设置页「系统监控」看 CUDA 状态
  # b) 后端日志
  docker logs bilinote-backend | grep -i cuda
  # c) 直接问引擎（最可靠）
  docker compose exec backend python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
  docker compose exec backend python -c "import onnxruntime as o; print(o.get_available_providers())"
  ```
  预期：第一条输出 `1`，第二条包含 `CUDAExecutionProvider`。转写时宿主机 `nvidia-smi` 也能看到 python 进程占显存。

**国内镜像**：GPU compose 支持 `BASE_REGISTRY` / `APT_MIRROR` / `PIP_INDEX` 这几个 build-arg。与 CPU 栈一样，`BASE_REGISTRY` 只需能拉到 `python:3.11-slim`，**不要求镜像源支持 `nvidia/cuda` 命名空间**。

**起来了但没走 GPU？** 依次排查：

1. 宿主机 `nvidia-smi` 是否正常；
2. NVIDIA Container Toolkit 是否装好（上面 `--gpus all` 测试是否通过）；
3. 容器内 `ldconfig -p | grep cublas` 是否有输出——没有说明 `ldconfig` 步骤没生效；
4. `docker logs bilinote-backend` 是否有 CUDA / cuDNN 报错（宿主机驱动需支持 CUDA 12.x）。

`fast-whisper` 本身的 GPU 依赖说明可参考：[faster-whisper 项目](https://github.com/SYSTRAN/faster-whisper#requirements)

## 🧠 路线图

- [x] 支持抖音、TikTok 及快手等视频平台
- [x] 前端可切换 AI 模型与语音转文字模型
- [x] AI 摘要风格自定义（学术风、口语风、重点提取等）
- [x] 基于 RAG 的笔记内容 AI 问答
- [x] 管理员 / 多用户登录与数据隔离
- [x] 硬字幕 OCR 提取（CPU / GPU）
- [x] 多用户任务调度与资源限额
- [ ] 笔记导出为 PDF / Word / Notion（当前仅支持 Markdown 与思维导图 SVG）

## 🔎 代码参考

- `抖音下载功能` 部分代码参考引用自 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)

## 📜 License

本项目基于 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) 二次开发，遵循 MIT License。原始版权声明：

```
MIT License

Copyright (c) 2024 Jeffery Huang
```

## 📚 相关文档

- [任务调度说明](backend/TASK_SCHEDULING.md)：多用户部署的并发、排队、取消和中断恢复规则。默认全站执行 6 个、每用户执行 1 个；GPU 推理 1 个、CPU 密集阶段 2 个、模型总结请求 2 个。
- [并发压测记录及参数选择依据](backend/CONCURRENCY_BENCHMARK.md)：参数选择依据、GPU 并发 1/2/3/4 对比、显存驻留实验和复现命令。
- [管理员登录与凭据保护](understanding/管理员登录与凭据保护.md)
- [用户数据隔离与旧历史迁移](understanding/用户数据隔离与旧历史迁移.md)
- [OCR 推理加速路线图](understanding/OCR_推理加速路线图.md)
- [OCR 视频到模型输入流程](understanding/OCR_视频到模型输入流程.md)
- [README 修改清单](understanding/README修改清单.md)
