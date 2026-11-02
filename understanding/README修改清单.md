# README 修改清单

> 生成日期：2026-11-01
> 核查对象：仓库根目录 `README.md`（改动前 511 行）
> 核查方式：逐节对照 `backend/`、`BillNote_frontend/`、`BillNote_extension/` 的实际实现

## 背景

本仓库 fork 自 [`JefferyHcool/BiliNote`](https://github.com/JefferyHcool/BiliNote)（MIT 协议）。
上游 README 中包含大量原作者的个人商业化内容与上游迭代历史，与当前仓库无关；同时上游 README 的品牌素材（Logo、截图）停留在旧版蓝色主题，与当前前端实际使用的 sage 绿主题不符。

本文档列出全部需要修改的点，按严重程度分为 A–E 五组。

---

## 核查依据（实测证据）

| 核查项 | README 呈现 | 代码实际 | 证据位置 |
| --- | --- | --- | --- |
| Logo 主色 | `#3C77FB`（蓝色，6 处） | `#6E7F64`（sage 绿，6 处） | `doc/icon.svg` vs `BillNote_frontend/src/assets/icon.svg` |
| 主题色变量 | 未说明 | `--primary: #6e7f64`、`--sage: #e4e7dc` | `BillNote_frontend/src/index.css:39,54` |
| 版本号 | `v2.4.4` | `tauri.conf.json` = **2.4.5**；`CHANGELOG.md` = **2.4.4**；`package.json` = `0.0.0` | 三处不一致 |
| 导出格式 | TODO 未勾选 PDF/Word/Notion | 仅 Markdown；`utils/export.py` 有 PDF 能力但**未被任何路由调用（死代码）** | `routers/history.py:39-46` |
| 转写器 | 列 4 种 | 实际 **5 种**（漏 kuaishou） | `backend/app/transcriber/` |
| 支持平台 | 列 5 种 | 实际 **6 个 key**（含 `tiktok`） | `backend/app/services/constant.py:7-14` |
| i18n | `CLAUDE.md` 声称有 `react-i18next` | **不存在**（无 `i18n/` 目录、无 `LanguageSwitcher.tsx`） | `find` 无结果 |
| 登录/多用户 | 仅 1 句带过 | 完整实现（PBKDF2 60 万次、Bearer token、任务级隔离） | `app/services/auth.py`、`storage_scope.py` |
| OCR 硬字幕 | 未提及 | 完整实现（RapidOCR 3.9.2 + CUDA，实测 6.1x 加速） | `backend/app/ocr/`、`OCR_GPU_VALIDATION.md` |

---

## 🔴 A. 商业化内容（必须删除）

以下全部为原作者个人变现渠道，与当前仓库无关。

| # | 章节 / 位置 | 内容 | 处理 |
| --- | --- | --- | --- |
| A1 | 标题下方推广块 | 「BiliNote Pro · 在线版」+ 3 个 CTA 按钮 + `www.bilinote.app` | **删除** |
| A2 | 「项目简介」内引用 | `> 💡 想直接用、不想本地部署？` 推广引用 | **删除** |
| A3 | `## 🌐 在线使用（推荐）` | 整节指向 `www.bilinote.app` | **删除** |
| A4 | `## 💼 企业AI落地咨询服务` | 整节，留微信号 `JefferyHJW` | **删除** |
| A5 | `## 📝 使用文档` | 指向私有文档站 `docs.bilinote.app` | **删除链接**，改为指向本仓库 `understanding/` |
| A6 | `## 📦 桌面版下载` | 指向原作者 Releases | 改为当前仓库 Releases，或删除 |
| A7 | `## 💎 BiliNote AI笔记系统一对一搭建服务` | 整节 + `doc/remote-install-wechat.png` | **删除** |
| A8 | `### Contact and Join-联系和加入社区` | 微信公众号二维码 + `doc/wechat-gzh.png` | **删除** |
| A9 | `## Buy Me a Coffee / 捐赠` | 2 张腾讯云 COS 收款码 | **删除** |
| A10 | `## ⭐ Star History` | 指向 `JefferyHcool/BiliNote` | 改仓库名或删除 |
| A11 | 顶部 badges | Star badge 指向 `jefferyhcool/BiliNote` | 改仓库名或删除 |
| A12 | `## 🔎代码参考` | 抖音下载功能参考 `Evil0ctal/Douyin_TikTok_Download_API` | **保留**（署名义务） |
| A13 | `## 📜 License` | MIT 声明 | **保留**（MIT 要求保留原作者版权） |
| A14 | 标题 | `BiliNote v2.4.4` 与 `tauri.conf.json` 的 `2.4.5` 不符 | 修正或去掉版本号 |

> **注意**：A12 与 A13 不可删。MIT 协议要求保留原始版权声明与致谢。

---

## 🔴 B. 品牌 / Logo / 配色不符

| # | 问题 | 处理 |
| --- | --- | --- |
| B1 | 头部引用 `./doc/icon.svg`（蓝色旧版 `#3C77FB`），与前端 `#6E7F64` 不一致 | 改用 `BillNote_frontend/src/assets/icon.svg`，或用后者覆盖 `doc/icon.svg` |
| B2 | 5 张截图（`doc/image1.png`、`image3.png`、`image.png`、`image4.png`、`image5.png`）均为**旧版蓝色 UI** | 需重新截图；当前 UI 特征：sage 绿主色、serif 标题、可折叠面板、明暗双主题 |
| B3 | 无设计语言说明 | 新增一节：sage 绿 `#6e7f64` / `#e4e7dc`、Tailwind v4、Radix + antd + `@ant-design/x` |
| B4 | `doc/` 下 18 个文件，其中 `wechat*.png`、`wechat.JPG`、`remote-install-wechat.png` 为商业素材 | 随 A7/A8/A9 一并清理 |
| B5 | `doc/image2.png`、`doc/BiliNote.png` 未被 README 引用 | 确认用途或清理 |

### doc/ 目录现状

```
BiliNote.png              ← 未被引用
icon.svg                  ← 蓝色旧版（B1）
image.png                 ← 旧版截图（B2）
image1.png                ← 旧版截图（B2）
image2.png                ← 未被引用
image3.png                ← 旧版截图（B2）
image4.png                ← 旧版截图（B2）
image5.png                ← 旧版截图（B2）
remote-install-wechat.png ← 商业素材（A7）
wechat-group-1~5.png      ← 商业素材（A8）
wechat-gzh.png            ← 商业素材（A8）
wechat.JPG / wechat.png   ← 商业素材（A8）
```

---

## 🟠 C. 与代码不符的功能声称

| # | README 声称 | 代码实际 | 处理 |
| --- | --- | --- | --- |
| C1 | 「支持 Bilibili、YouTube、本地视频、抖音、快手」 | ✅ 正确，另支持 `tiktok` | 补上 TikTok |
| C2 | 「Fast-Whisper、MLX-Whisper、Groq、BCut」 | ⚠️ 漏了 **kuaishou** 转写器 | 补上快手 |
| C3 | 「基于 RAG 的 AI 问答」 | ✅ 正确 | — |
| C4 | TODO「笔记导出为 PDF / Word / Notion」 | ✅ 正确未实现。实际仅 Markdown + 思维导图 SVG | 保持未勾选，加注「当前仅支持 Markdown / SVG」 |
| C5 | v2.0.0 → v2.3.0 共 **12 个版本更新日志** | 均为**上游**迭代史，与本 fork 无关 | **删除或折叠**，改为「详见 [CHANGELOG.md](../CHANGELOG.md)」 |
| C6 | 版本号 `v2.4.4` | `tauri.conf.json` = 2.4.5，`CHANGELOG.md` = 2.4.4，`package.json` = 0.0.0 | 三处对齐 |
| C7 | 登录/多用户隔离仅 1 句带过 | 本 fork 核心功能，实现完整 | **新增专章**（见 E1） |
| C8 | 未提及 OCR 硬字幕提取 | 完整实现，含 GPU 加速与压测报告 | **新增专章**（见 E2） |
| C9 | 未提及任务调度/限流 | `task_scheduler.py` + 两份文档 | 文末补链接（见 E3） |

---

## 🟡 D. 结构性问题

| # | 问题 | 处理 |
| --- | --- | --- |
| D1 | 「方式一：Docker 部署」与「🐳 使用 Docker 一键部署」内容重复 | 合并为一处 |
| D2 | 「⚙️ 依赖说明」夹在两种部署方式之间，打断阅读流 | 移到部署章节之后 |
| D3 | 「🚀 快速开始」之前有 73 行商业化内容（占全文 14%） | 删除后简介可扩写至 10–15 行 |
| D4 | 「📸 截图预览」5 张图连排无说明 | 加图注（主界面 / 设置页 / 思维导图 / AI 问答） |
| D5 | 全文 511 行，其中约 130 行是商业化 + 上游历史 | 精简至 250–300 行 |
| D6 | 缺少架构说明（`CLAUDE.md` 有完整版，README 无） | 新增「技术架构」一节 |
| D7 | 缺少 fork 合规声明 | 加一句「本项目 fork 自 JefferyHcool/BiliNote，遵循 MIT 协议」 |

---

## 🟢 E. 建议新增章节（基于本仓库实际实现）

| # | 章节 | 可引用的实现 |
| --- | --- | --- |
| E1 | **多用户与权限** | `app/services/auth.py`（PBKDF2-SHA256 60 万次迭代、密码 12–128 字符、Bearer token、12h / 记住我 30d）、`routers/auth.py`、`SettingPage/Users.tsx`、`storage_scope.py`（ContextVar 任务级隔离）、`legacy_migration.py`（旧数据归原管理员） |
| E2 | **硬字幕 OCR** | `app/ocr/`（RapidOCR 3.9.2 + ppocrv6）、`OCR_DEVICE=cpu/cuda`（不回退 CPU）、GPU 实测 6.1x 加速（`OCR_GPU_VALIDATION.md`）、并发报告（`OCR_CONCURRENCY_REPORT_2026-10-31.md`） |
| E3 | **任务调度与限流** | `TaskLimits`（全站 6 / 每用户 1 / 每用户排队 3 / 全站排队 20 / GPU 1 / CPU 2 / API 2）、`TASK_SCHEDULING.md`、`CONCURRENCY_BENCHMARK.md` |
| E4 | **前端设计语言** | sage 绿 `#6e7f64`、浅绿 `#e4e7dc`、Tailwind v4、Radix UI + antd + `@ant-design/x`、serif 标题、明暗双主题 |
| E5 | **技术架构** | 后端：`routers` / `services` / `downloaders` / `transcriber` / `ocr` / `gpt` / `db`；前端：React 19 + Vite + Zustand；插件：Vue 3 + MV3；桌面端：Tauri 2 |

---

## 待确认事项

1. **当前仓库地址**：`git remote` 显示 `origin` 为 `https://github.com/wlj666666/bilinote.git`，`upstream` 为上游。
   → 需确认 README 中的仓库名、clone 命令、badges、Releases 链接统一改为哪个。
2. **文档语言**：纯中文 / 中英双语。
3. **商业化内容处理方式**：直接删净，或保留一段「上游项目致谢」。
4. **截图更新**：是否需要重新截图（需运行前端），或先仅修正 Logo 路径。

---

## 建议执行顺序

1. 删除商业化内容（A1–A11）—— 一次性去掉约 130 行噪音
2. 修正 Logo 与截图（B1–B2）
3. 对齐版本号（A14 / C6）
4. 补充核心功能章节（E1–E3）—— 本 fork 的价值所在
5. 重排结构（D1–D5）
6. 补充架构与合规说明（D6 / D7 / E5）

---

## 附：核查用命令

```bash
# Logo 配色对比
grep -oE "#[0-9a-fA-F]{6}" doc/icon.svg | sort | uniq -c
grep -oE "#[0-9a-fA-F]{6}" BillNote_frontend/src/assets/icon.svg | sort | uniq -c

# 前端主题色
grep -n "primary\|sage" BillNote_frontend/src/index.css

# 版本号三处对比
grep '"version"' BillNote_frontend/src-tauri/tauri.conf.json
grep '"version"' BillNote_frontend/package.json
head -10 CHANGELOG.md

# 支持的转写器
ls backend/app/transcriber/*.py | xargs -n1 basename

# 支持的平台
sed -n '1,20p' backend/app/services/constant.py

# 导出能力核实
grep -n "media_type\|Content-Disposition" backend/app/routers/history.py
grep -rn "ExportUtils" backend/app/routers/ backend/app/services/   # 无输出 = 死代码

# i18n 是否存在（CLAUDE.md 声称有，实际无）
find BillNote_frontend/src -name "*i18n*" -o -name "locales" -type d

# 仓库来源
git remote -v
```
