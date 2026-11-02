# OCR profiling 与并发实验

诊断入口为 `scripts/profile_ocr.py`，采集与解析模块为 `app/ocr/profiling.py`。复用
`OCREngine.read()` 和现有 RapidOCR 的检测、方向分类、识别及预处理，默认业务任务不开启 profiling。
模型与输入均使用本地文件，不调用大模型 API，不创建任务记录，不改变生产 GPU=1 的调度限制。

实测分析见 [2026-10-30 报告](OCR_PROFILING_REPORT_2026-10-30.md)。

## 启动环境

在 backend 目录运行，使用已有 bilinote Python 环境。CUDA 实验通过 `start-gpu.sh` 加载相同的
cuBLAS/cuDNN 动态库；如果当前 `python` 不属于该环境，显式设置 `BILINOTE_PYTHON`。

```bash
conda activate bilinote
cd backend
python scripts/profile_ocr.py --help
```

## 1. 从一个视频构建固定输入

```bash
python scripts/profile_ocr.py prepare VIDEO.mp4 \
  --dataset data/ocr_profile/my-run/frames --count 96 \
  --roi 0.10 0.79 0.90 0.92
```

在全视频范围内均匀取样，保存 RGB NumPy 数组、实际帧时间、文件哈希和视频哈希。
`--start`、`--end` 可限定取样范围。ROI 是人工选择的归一化字幕区域，需按素材调整。
这里的 ROI 对照用于度量已知字幕区域的工作量，不代替生产流程的自动字幕定位。

## 2. 关闭 profiling 测稳态性能

```bash
bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_profile/my-run/frames --output data/ocr_profile/my-run/full \
  --label baseline --levels 1,2,3,4 --repeats 3
```

并发 N 表示 N 个独立进程，每个进程持有一套 OCR 引擎；每组总图片数相同，按编号交错分配。
读图、模型初始化及预热发生在同步开始之前，不计入稳态耗时。轮次间反转并发测试顺序，以减轻
预热和温度顺序偏差。代码不把非线程安全的 RapidOCR 实例共享给多个线程。
预热只覆盖每个进程前三个自然样本，未穷举所有动态形状；测量中仍包含新形状首次执行的开销。

首组测试前检查是否存在其他 GPU 计算进程；实验期间显存使用超过 91%、GPU 温度达到 85℃、
主机可用内存低于约 2 GiB 或达到超时就中止当前组。采样间隔约 0.5 秒，不能捕获全部瞬时峰值。
默认 `--timeout 600`，包含模型加载和开始屏障等待。已有输出目录不会覆盖。

其他配置：

```bash
# 已知字幕区域；应先生成自己的串行基线
bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_profile/my-run/frames --output data/ocr_profile/my-run/roi \
  --label roi-baseline --crop roi --levels 1,2,3,4 --repeats 3

# 明确为正向字幕的输入，可以验证是否需要方向分类
bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_profile/my-run/frames --output data/ocr_profile/my-run/no-cls \
  --label no-cls --no-cls --levels 1 --repeats 3
```

还可单独改变 `--det-side`、`--threads`、`--rec-batch`、`--tf32`。
每次先改变一个因素，比较文本再考虑采用。检测尺寸、方向分类等变化可能影响结果；
当前验证不等于所有输入上的准确率保证。`--device cpu` 支持 CPU 路线检查。

**实际检测尺寸：** RapidOCR 3.9.2 在 `max` 模式下会选择 960/1500/2000 动态档位，
覆盖 `Det.limit_side_len`。因此 `--det-side 960` 本身可能没有实际效果。
诊断开关 `--force-det-side` 才会覆盖预处理选择，真正测试指定上限，并记录 `.run`
的输入张量尺寸。此覆盖只在显式指定的隔离实验中生效，不改变生产默认行为。

一键单因素矩阵（每项默认两轮，串行逐项运行）：

```bash
bash start-gpu.sh scripts/profile_ocr.py matrix \
  --dataset data/ocr_profile/my-run/frames --output data/ocr_profile/my-run/tuning \
  --variants no-cls,batch16,tf32,det960,roi-no-cls --repeats 2
```

另可选择 `det1280-effective`、`det960-effective`、`no-cls-tf32`。
前两项自动使用 `--force-det-side`。缩小检测输入后需检查漏检、分组及字幕文本变化。

## 3. 少量真实输入采集 ORT profiling

```bash
bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_profile/my-run/frames --output data/ocr_profile/my-run/traces \
  --label baseline --levels 1,2 --repeats 1 --profile --limit 12
```

三个模型在初始化时注入启用 profiling 的 ORT session，保留 RapidOCR 的图优化、线程、
provider 与 CUDA 参数。使用 RapidOCR 3.9.2 的自定义 session 入口；适配器中的 `_initialize`
和 `_init_sess_opts` 依赖该版本内部接口，升级 RapidOCR 后需重跑诊断回归。

输出：

```text
每组实验目录/
  config.json            输入身份与参数
  telemetry.json         GPU 显存、利用率、温度、主机可用内存
  result.json            墙钟耗时、延迟、阶段统计、逐图文字
  host-timeline.json     应用阶段时间线（宿主侧，包含等待）
  worker-N/
    worker.log
    result.json          每进程阶段明细与 ORT 汇总
    profiles/
      det_*.json         ORT 原始检测执行记录
      cls_*.json         ORT 原始分类执行记录
      rec_*.json         ORT 原始识别执行记录
```

`worker-N/result.json` 的 `stage_rows[].input_shapes` 保存各次实际输入尺寸，
可统计 batch 次数、宽度和总输入元素量，检查“大 batch 是否因 padding 增加计算”。
若实验命令或输入有误，在该组目录添加含原因的 `excluded.json`；汇总排除该组并列出原因，
保留原始文件。`roi-*` 标签要求显式传入 `--crop roi`，避免标错实验。

Chrome trace 格式的 JSON 可用本地时间线查看器/Perfetto 打开。原始 ORT profile 包含预热；
应用阶段统计排除预热。不同 session 的时间原点不应未经校准直接合并。

**解释范围：** `CUDAExecutionProvider` 节点事件证明该次节点由 CUDA 后端执行。
节点 `dur` 是 ORT 宿主侧事件时间，不能当作 CUDA kernel-only 时间、SM 利用率或显存带宽利用率。
事件数量也不是算力占比。多进程时间重叠，累计阶段时间不能直接当作总墙钟时间。
细查 CPU/GPU 重叠、传输和同步使用 Nsight Systems；确认特定 kernel 瓶颈后再用 Nsight Compute。

## 4. 完整字幕流程验证

```bash
bash start-gpu.sh scripts/profile_ocr.py video VIDEO.mp4 \
  --output data/ocr_profile/my-run/video-baseline.json
```

此模式运行完整自动定位、默认 5 fps 提取、图像复用、合并、局部复查与质量门禁，
显式注入引擎以便诊断，不经过任务队列。用于无人争用的隔离实验。
单独更改 `--no-cls`、`--det-side` 等配置后，用新输出路径比较。
`--no-reuse` 可验证视觉复用收益；`--profile` 可导出完整流程的模型 trace，但文件会变大，
profiling 性能不与关闭时混为一组。正式比较应在关闭 profiling 时进行。

视频报告含 `experiment` 参数、源视频哈希、冷启动总时间、分阶段时间、OCR 次数及复用帧数。
检测和识别成功不等于字幕质量合格：质量门禁未通过时返回非零状态，保留诊断与部分原文。

输出不一致时，可在相同真实帧和自动定位ROI上只切换方向分类，记录分类角度与置信度：

```bash
bash start-gpu.sh scripts/profile_ocr.py review VIDEO.mp4 \
  --roi-report data/ocr_profile/my-run/video-baseline.json \
  --times 59.4 112.8 364.2 \
  --output data/ocr_profile/my-run/angle-review.json
```

此命令保存裁剪图片，TF32保持关闭，分别记录开/关方向分类后的文本，便于核对是否发生
正向字幕误判180度等问题。默认质量门禁通过也可能存在局部错字或漏句，串行基线不等于人工真值。
完整视频和该复查命令同样有GPU空闲检查、显存/温度保护和进程超时。

## 5. 自动汇总与保存环境

```bash
bash start-gpu.sh scripts/profile_ocr.py environment \
  --output data/ocr_profile/my-run/environment.json
python scripts/profile_ocr.py report --root data/ocr_profile/my-run \
  --output data/ocr_profile/my-run/SUMMARY.md
```

汇总按配置/并发/profiling 分组，输出耗时及吞吐中位数、P95、显存峰值、阶段计时。
同一裁剪方式的字幕文本与串行基线对照，机器可读 JSON 另列非空字幕比例。
这只是输出一致性检查，不是人工标注准确率；新旧结果不一致时须逐例检查。

`backend/data` 被 Git 忽略，原始数组和 trace 要另行归档。实验分析和带哈希的精简记录放在
`backend/benchmark_records/`，便于和代码一起保留。

测试：

```bash
python -m unittest discover -s tests -p test_ocr_profiling.py -v
```
