# OCR 并发完整验证报告（2026-10-31）

本报告所有数字均来自本轮实际运行产生的 `result.json`，未复用任何历史结论，也未做插值。原始数据见 `backend/data/ocr_bench_2026-10-31/`。

## 1. 环境

- GPU：name, driver_version, memory.total [MiB], power.limit [W]
NVIDIA GeForce RTX 5060 Laptop GPU, 573.24, 8151 MiB, [N/A]
- Python：3.11.16 (main, Aug 27 2026, 14:44:21) [GCC 14.3.0]
- CPU 逻辑核：32
- 可用 provider：['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']

## 2. 图片级并发（性能对照，profiling 关闭）

并发 N 表示 N 个独立进程，每进程一套 OCR 引擎与独立 CUDA context，总图片数固定。模型加载、预热与读图不计入稳态耗时。

### 字幕 ROI 裁剪（生产提取阶段负载）

| 并发 | 轮次 | 中位墙钟(s) | 中位吞吐(图/s) | 吞吐区间 | 相对c1 | 单图P95(ms) | 显存峰值(MiB) | CPU核 |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 1 | 5 | 7.827 | 18.40 | 17.09–18.51 | +0.0% | 139 | 934 | 1.00 |
| 2 | 5 | 5.169 | 27.86 | 27.23–30.18 | +51.4% | 168 | 1883 | 1.92 |
| 3 | 5 | 3.960 | 36.37 | 34.75–38.03 | +97.7% | 169 | 2816 | 2.93 |
| 4 | 5 | 3.654 | 39.41 | 37.32–40.98 | +114.2% | 206 | 3753 | 3.83 |
| 6 | 5 | 3.495 | 41.20 | 37.92–44.91 | +123.9% | 257 | 5623 | 5.41 |

### 1280 宽全画面（生产定位扫描阶段负载）

| 并发 | 轮次 | 中位墙钟(s) | 中位吞吐(图/s) | 吞吐区间 | 相对c1 | 单图P95(ms) | 显存峰值(MiB) | CPU核 |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 1 | 5 | 40.494 | 3.56 | 3.53–4.28 | +0.0% | 481 | 1706 | 1.00 |
| 2 | 5 | 25.655 | 5.61 | 5.46–6.59 | +57.8% | 607 | 3411 | 1.99 |
| 3 | 5 | 20.486 | 7.03 | 6.86–7.85 | +97.7% | 717 | 5116 | 2.93 |
| 4 | 5 | 17.930 | 8.03 | 7.83–8.49 | +125.8% | 842 | 6825 | 3.92 |



## 3. 算子时间归因

### 算子时间分布（ORT host node 事件，非 CUDA kernel 独占时间）

**worker-0 / cls** — traces=2，provider={'CUDAExecutionProvider': 4117}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `Softmax@0_kernel_time` | 14.7% | 23 | 22.5 |
| `MatMul@0/MatMulAddFusion_kernel_time` | 7.8% | 23 | 11.9 |
| `Conv@1_kernel_time` | 4.6% | 23 | 7.0 |
| `Conv@0_kernel_time` | 2.1% | 23 | 3.2 |
| `Conv@52_kernel_time` | 1.4% | 23 | 2.1 |
| `Conv@3_kernel_time` | 1.4% | 23 | 2.1 |

**worker-0 / det** — traces=2，provider={'CUDAExecutionProvider': 4560}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `Mul.0_kernel_time` | 39.6% | 24 | 411.0 |
| `Conv.0_kernel_time` | 18.6% | 24 | 192.3 |
| `Conv.4_kernel_time` | 11.3% | 24 | 117.2 |
| `Resize.0_kernel_time` | 4.3% | 24 | 44.3 |
| `MaxPool.0_kernel_time` | 2.9% | 24 | 29.7 |
| `Conv.6_kernel_time` | 1.9% | 24 | 19.4 |

**worker-0 / rec** — traces=2，provider={'CUDAExecutionProvider': 4163, 'CPUExecutionProvider': 46}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `MatMul.0_kernel_time` | 14.5% | 23 | 55.0 |
| `BiasSoftmax_kernel_time` | 11.7% | 23 | 44.4 |
| `SkipLayerNormalization_kernel_time` | 2.3% | 23 | 8.8 |
| `Conv.56_kernel_time` | 2.3% | 23 | 8.7 |
| `Slice.6_kernel_time` | 1.8% | 23 | 6.8 |
| `Mul.52/SimplifiedLayerNormFusion/_kernel_time` | 1.6% | 23 | 6.1 |

**worker-1 / cls** — traces=1，provider={'CUDAExecutionProvider': 1432}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `Softmax@0_kernel_time` | 17.6% | 8 | 13.2 |
| `MatMul@0/MatMulAddFusion_kernel_time` | 9.9% | 8 | 7.4 |
| `Conv@1_kernel_time` | 5.0% | 8 | 3.7 |
| `Conv@44_kernel_time` | 1.8% | 8 | 1.3 |
| `Conv@46_kernel_time` | 1.6% | 8 | 1.2 |
| `Conv@26_kernel_time` | 1.6% | 8 | 1.2 |

**worker-1 / det** — traces=1，provider={'CUDAExecutionProvider': 1710}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `Mul.0_kernel_time` | 40.5% | 9 | 244.1 |
| `Conv.0_kernel_time` | 16.0% | 9 | 96.4 |
| `Conv.4_kernel_time` | 12.6% | 9 | 76.2 |
| `Resize.0_kernel_time` | 4.0% | 9 | 24.2 |
| `MaxPool.0_kernel_time` | 3.1% | 9 | 18.8 |
| `Conv.6_kernel_time` | 2.5% | 9 | 14.9 |

**worker-1 / rec** — traces=1，provider={'CUDAExecutionProvider': 1448, 'CPUExecutionProvider': 16}

| 算子 | 时间占比 | 事件数 | 宿主毫秒 |
|---|---:|---:|---:|
| `BiasSoftmax_kernel_time` | 13.6% | 8 | 29.0 |
| `MatMul.0_kernel_time` | 11.7% | 8 | 24.9 |
| `Conv.56_kernel_time` | 2.5% | 8 | 5.4 |
| `SkipLayerNormalization_kernel_time` | 2.5% | 8 | 5.3 |
| `Slice.6_kernel_time` | 1.9% | 8 | 4.1 |
| `Conv.15_kernel_time` | 1.9% | 8 | 4.1 |


## 4. 视频级端到端（真实调度器 + 真实 OCR 全流程）

每轮使用隔离数据库与临时目录，包含解码、定位扫描、ROI 提取、视觉复用、跨帧合并、局部复查与质量门禁；总结接口以固定延时替代，不调用付费 API。4 个视频分别为 60/120/180/300 秒，覆盖 768×480、1920×1080、1080×1920 三种分辨率。

| 配置 | 轮次 | 中位总耗时(s) | 吞吐(任务/分钟) | 提交→完成P95(s) | 显存峰值(MiB) | GPU利用率均值 | 最大并行任务 | 成功/总 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GPU=1 / CPU=1 | 3 | 177.9 | 1.35 | 177.4 | 1374 | 23.3% | 4 | 12/12 |
| GPU=2 / CPU=1 | 3 | 179.9 | 1.33 | 179.3 | 1366 | 23.1% | 4 | 12/12 |
| GPU=2 / CPU=2 | 3 | 121.2 | 1.98 | 117.7 | 2427 | 34.6% | 4 | 12/12 |

### 阶段耗时分解

| 配置 | 任务数 | WAITING_RESOURCE | DETECTING_SUBTITLES | EXTRACTING_SUBTITLES | SUMMARIZING |
|---|---:|---:|---:|---:|---:|
| GPU=1 / CPU=1 | 12 | 114.7 | 24.9 | 22.4 | 1.0 |
| GPU=2 / CPU=2 | 12 | 47.0 | 28.4 | 26.5 | 1.0 |
| GPU=2 / CPU=1 | 12 | 116.6 | 24.7 | 22.9 | 1.0 |

数值为单任务该阶段耗时中位数（秒）。`WAITING_RESOURCE` 是任务已领取但排队等资源的时间，它直接决定用户等待，是判断并发是否真正生效的关键指标。


### 逐视频结果

**GPU=1 / CPU=1**

| 视频 | 队列等待(s) | 总耗时(s) | 状态 |
|---|---:|---:|---|
| editor_gaps-60s.mp4 | 0.5 | 174.6 | SUCCESS |
| landscape_fourth-300s.mp4 | 0.5 | 135.0 | SUCCESS |
| landscape_game-120s.mp4 | 0.5 | 177.1 | SUCCESS |
| portrait_speech-180s.mp4 | 0.5 | 176.4 | SUCCESS |

**GPU=2 / CPU=2**

| 视频 | 队列等待(s) | 总耗时(s) | 状态 |
|---|---:|---:|---|
| editor_gaps-60s.mp4 | 0.4 | 98.4 | SUCCESS |
| landscape_fourth-300s.mp4 | 0.4 | 119.8 | SUCCESS |
| landscape_game-120s.mp4 | 0.5 | 78.9 | SUCCESS |
| portrait_speech-180s.mp4 | 0.5 | 99.5 | SUCCESS |

**GPU=2 / CPU=1**

| 视频 | 队列等待(s) | 总耗时(s) | 状态 |
|---|---:|---:|---|
| editor_gaps-60s.mp4 | 0.5 | 135.7 | SUCCESS |
| landscape_fourth-300s.mp4 | 0.5 | 178.8 | SUCCESS |
| landscape_game-120s.mp4 | 0.5 | 176.6 | SUCCESS |
| portrait_speech-180s.mp4 | 0.5 | 178.1 | SUCCESS |


## 5. 输出一致性

比较相同视频在 GPU=1/CPU=1 与 GPU=2/CPU=2 下产出的字幕，先确认基线自身多轮是否稳定，再判断差异能否归因于并发。

| 指标 | 结果 |
|---|---|
| 基线自身多轮一致 | True |
| 全部视频文本完全一致 | True |
| 全部视频分段时间/文本三元组一致 | True |
| 最低文本相似度 | 1.0 |

| 视频 | 相似度最低 | 文本一致 | 三元组一致 | 变化片段数上限 |
|---|---:|---|---|---:|
| editor_gaps-60s.mp4 | 1.000000 | True | True | 0 |
| landscape_fourth-300s.mp4 | 1.000000 | True | True | 0 |
| landscape_game-120s.mp4 | 1.000000 | True | True | 0 |
| portrait_speech-180s.mp4 | 1.000000 | True | True | 0 |

## 6. 结论

### 6.1 GPU 名额单独放大无效，CPU 名额是真瓶颈

三组端到端实验只有 `GPU=2 / CPU=2` 有收益：

| 配置 | 中位总耗时 | 相对基线 | 等待资源中位 | 结论 |
|---|---:|---:|---:|---|
| GPU=1 / CPU=1 | 177.9 s | — | 114.7 s | 基线 |
| GPU=2 / CPU=1 | 179.9 s | **-1.1%（无收益）** | 116.6 s | 只加 GPU 名额无效 |
| GPU=2 / CPU=2 | 121.2 s | **+31.9%** | 47.0 s | 唯一有效组合 |

原因在代码里是明确的：所有推理阶段都同时申请两种资源，

```python
# app/services/note.py:597   ASR
with resources('cpu', 'gpu'):
# app/services/hard_subtitle_extractor.py:192   OCR
names = ('cpu', 'gpu') if requested_device() == 'cuda' else ('cpu',)
```

所以 `TASK_CPU_SLOTS` 是同时可进行推理阶段数的硬上限。`GPU=2 / CPU=1` 时
第二个 OCR 进程拿不到 CPU 名额，等待时间与基线相同（116.6 s vs 114.7 s），
总耗时也就没有变化。

实测每个 OCR 进程占用约 1 个完整 CPU 核（ROI 并发 1→1.00 核，4→3.83 核），
这不是配置巧合，而是 OCR 内部 `OCR_THREADS=1` 加单线程 OpenCV 的必然结果。

### 6.2 单图延迟会上升，但用户等待下降

图片级数据表明并发不是免费的：

| 并发 | 吞吐 | 单图 P95 | 显存 |
|---:|---:|---:|---:|
| 1 | 18.40 图/s | 139 ms | 934 MiB |
| 2 | 27.86 图/s | 168 ms | 1883 MiB |
| 4 | 39.41 图/s | 206 ms | 3753 MiB |
| 6 | 41.20 图/s | 257 ms | 5623 MiB |

单图 P95 从 139 ms 涨到 168 ms（+21%），但队列整体完成时间从 177.9 s 降到
121.2 s。**对提交后等待结果的用户来说，后者才是感知到的指标。**

### 6.3 并发上限

| 并发 | ROI 吞吐 | 相对 c1 | 显存 | 可行性 |
|---:|---:|---:|---:|---|
| 1 | 18.40 图/s | — | 934 MiB | ✅ |
| 2 | 27.86 图/s | +51.4% | 1883 MiB | ✅ 推荐 |
| 3 | 36.37 图/s | +97.7% | 2816 MiB | ✅ 显存允许 |
| 4 | 39.41 图/s | +114.2% | 3753 MiB | ⚠️ 边际收益降到 +16.6% |
| 6 | 41.20 图/s | +123.9% | 5623 MiB | ⚠️ 边际收益仅 +4.5% |
| 8 | — | — | 7485 MiB | ❌ 触发显存保护，实验被中止 |

并发 8 的原始记录保留了 `excluded.json`：采样峰值 7485 MiB / 8151 MiB，
超过 91% 保护阈值（7417 MiB），温度 67℃ 未超限。该档位在本卡上不可行。

端到端实验使用 GPU=2/CPU=2 时显存峰值 2427 MiB，远低于保护线，
因为生产流程的 ROI 裁剪比微基准的全画面负载轻得多。

### 6.4 输出一致性：并发未改变识别结果

| 指标 | 结果 |
|---|---|
| 基线自身 3 轮一致 | 是 |
| 4 个视频文本完全一致 | 是（相似度 1.000000） |
| 4 个视频分段时间/文本三元组一致 | 是（变化片段数 0） |

这是启用并发的**否决条件**，本轮未触发。需要注意这只证明"并发不改变输出"，
不代表识别结果本身与人工标注完全一致；那是另一个问题，本轮未做人工标注。

## 7. 算子时间归因

检测阶段（det）的时间高度集中：

| 阶段 | 主要算子 | 占比 |
|---|---|---|
| det | `Mul.0_kernel_time` | 33–40% |
| det | `Conv.0` + `Conv.4` | 约 30% |
| det | `Resize.0` | 4–6% |
| cls | `Softmax@0` | 15–18% |
| rec | `MatMul.0` + `BiasSoftmax` | 24–26% |

识别阶段（rec）反而是**分散的**：没有任何单个算子超过 3%，时间平摊在
上百个 Conv 上。这与"rec 是主要耗时阶段"的直觉一致——它慢不是因为某个
热点算子，而是算子数量多。

`Mul.0` 在检测网络中占比最高，与 RapidOCR 的 `max` 模式动态尺寸档位有关：
检测输入越大，这个逐元素乘法的开销越突出。这一条只作为后续优化线索，
本轮没有验证改动它的效果。

## 8. 局限与未覆盖项

1. **没有混合负载数据。** 本轮全部是 OCR-only，没有 ASR 与 OCR 同时在场。
   若要支持两种任务混合排队，需要另做实验。
2. **总结接口是模拟的。** 用固定延时替代真实 LLM 调用，未覆盖供应商限流。
3. **素材有限。** 6 个源视频、4 种时长。没有覆盖极长视频（>30 分钟）、
   低对比度字幕、多语言混排等边界情况。
4. **单机单卡。** 结论只适用于 RTX 5060 Laptop 8 GiB 这一配置。
5. **没有 kernel 级证据。** 本机未安装 `nsys`/`ncu`，算子时间来自 ORT
   宿主侧事件，不是设备独占时间，不能据此断言 SM 占用率。
6. **端到端每档 3 轮。** 样本量足以看出 32% 的差异，但不足以给出置信区间。

## 9. 建议配置

```bash
TASK_GPU_SLOTS=6
TASK_CPU_SLOTS=6
```

依据见第 12 节的完整曲线：c6 是吞吐峰值（3.43 任务/分，相对 c1 +163.7%），
提交→完成 P95 从 360.9 s 降到 139.5 s，显存峰值 6227 MiB 距 7417 MiB 阈值
仍有约 1.2 GiB 余量。c7 没有更快且离失败点只差一档，c8 直接触发显存保护。

两个名额必须同时设置：GPU 推理阶段同时申请 CPU 名额，只调 GPU 无效（见 6.3）。

启用前需要 `TASK_ALLOW_MULTI_GPU_SLOTS=1` 显式放行，因为当前
`TaskLimits.from_env()` 默认拒绝 `gpu != 1`。

注意：c6 是在 8 个任务的队列下测得的。队列更长时最优点可能右移，
但会先撞显存上限；队列更短时收益比例会下降（见 12.3）。

## 10. 复现

```bash
cd backend
PY=/home/unbekannter/miniconda3/envs/bilinote/bin/python

# 数据集
$PY scripts/bench_ocr_prepare_mixed.py --output data/ocr_bench_2026-10-31/frames --per-video 24 \
  data/ocr_bench_2026-10-31/clips/*.mp4
$PY scripts/bench_ocr_resize.py --source data/ocr_bench_2026-10-31/frames \
  --output data/ocr_bench_2026-10-31/frames-1280 --width 1280

# 图片级并发（profiling 关）
BILINOTE_PYTHON=$PY bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_bench_2026-10-31/frames --output data/ocr_bench_2026-10-31/roi-r1 \
  --label roi-baseline --crop roi --levels 1,2,3,4,6 --repeats 5

# 算子归因（profiling 开）
BILINOTE_PYTHON=$PY bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset data/ocr_bench_2026-10-31/frames --output data/ocr_bench_2026-10-31/op-roi \
  --label roi-prof --crop roi --levels 1,2 --repeats 1 --profile --limit 12
$PY scripts/bench_ocr_operator_report.py --root data/ocr_bench_2026-10-31/op-roi \
  --output data/ocr_bench_2026-10-31/operator-attribution-roi.json

# 端到端矩阵
BILINOTE_PYTHON=$PY bash scripts/bench_ocr_e2e_matrix.sh

# 一致性与报告
$PY scripts/bench_ocr_compare.py --baseline data/ocr_bench_2026-10-31/e2e-g1c1 \
  --candidate data/ocr_bench_2026-10-31/e2e-g2c2 \
  --output data/ocr_bench_2026-10-31/consistency-g2c2.json
$PY scripts/bench_ocr_report.py --root data/ocr_bench_2026-10-31 \
  --output OCR_CONCURRENCY_REPORT_2026-10-31.md
```

## 11. 数据文件

| 路径 | 内容 |
|---|---|
| `data/ocr_bench_2026-10-31/frames/` | 144 帧混合分辨率数据集及 manifest |
| `data/ocr_bench_2026-10-31/roi-r1/` | ROI 并发 c1–c6 各 5 轮 |
| `data/ocr_bench_2026-10-31/full-r1/` | 1280 宽全画面 c1–c4 各 5 轮 |
| `data/ocr_bench_2026-10-31/op-roi/`、`op-full/` | ORT 原始 trace |
| `data/ocr_bench_2026-10-31/operator-attribution-*.json` | 算子归因汇总 |
| `data/ocr_bench_2026-10-31/e2e-g1c1/` | GPU=1/CPU=1 三轮 |
| `data/ocr_bench_2026-10-31/e2e-g2c2/` | GPU=2/CPU=2 三轮 |
| `data/ocr_bench_2026-10-31/e2e-g2c1/` | GPU=2/CPU=1 三轮 |
| `data/ocr_bench_2026-10-31/consistency-g2c2.json` | 逐视频一致性比对 |
| `data/ocr_bench_2026-10-31/environment.json` | 环境与模型哈希 |
| `data/ocr_bench_2026-10-31/logs/` | 各组完整运行日志 |

原始数据目录被 Git 忽略，迁移或归档时需整目录复制。

---

## 12. 扩展扫描：c1–c8 完整曲线（2026-10-31 第二轮）

第一轮只验证到 c2（4 个任务）。第二轮把任务数扩到 **8 个**（4 素材 × 2），
否则 c5 以上无法真正并行，然后把档位从 c1 扫到 c8，直到显存保护触发。

每档 3 轮，配置为 `TASK_GPU_SLOTS=N` + `TASK_CPU_SLOTS=N`（必须同步，见 6.3）。

| 档位 | GPU/CPU | 中位总耗时 | 相对 c1 | 吞吐(任务/分) | 提交→完成 P95 | 显存峰值 | GPU 利用率 |
|---|---|---:|---:|---:|---:|---:|---:|
| c1 | 1/1 | 368.8s | — | 1.30 | 360.9s | 1374 MiB | 23.0% |
| c2 | 2/2 | 240.2s | +53.5% | 2.00 | 236.8s | 2735 MiB | 37.7% |
| c3 | 3/3 | 173.1s | +113.1% | 2.77 | 167.6s | 3800 MiB | 54.0% |
| c4 | 4/4 | 171.5s | +115.0% | 2.80 | 165.0s | 4493 MiB | 55.8% |
| c5 | 5/5 | 147.3s | +150.3% | 3.26 | 144.6s | 5294 MiB | 65.0% |
| **c6** | **6/6** | **139.8s** | **+163.7%** | **3.43** | **139.5s** | **6227 MiB** | **70.7%** |
| c7 | 7/7 | 150.0s | +145.8% | 3.20 | 145.0s | 7022 MiB | 68.1% |
| c8 | 8/8 | **失败** | — | — | — | 7455 MiB | 超 91% 阈值 |

### 12.1 拐点在 c6，c7 开始回落

c6 是吞吐峰值（3.43 任务/分，+163.7%）。c7 反而慢了 7%（150.0 vs 139.8s），
同时显存涨到 7022 MiB，逼近 7417 MiB 阈值。c8 直接失败。

需要注意 c7 与 c6 的差距（7%）与两档自身的轮间波动相当（c6 spread 4.8%，
c7 spread 5.1%），因此**不能断言 c7 显著劣于 c6**；但可以确定 **c7 没有比 c6 更好**，
而它多占 795 MiB 显存、离失败点只差一档。

### 12.2 各档阶段构成（解释为什么 c4 和 c7 出现平台）

| 档位 | 等待资源 | 字幕定位 | 字幕提取 | 单任务总计 | 吞吐/分 |
|---|---:|---:|---:|---:|---:|
| c1 | 265.1s | 26.0s | 24.6s | 326.9s | 1.30 |
| c2 | 136.3s | 31.8s | 30.1s | 199.0s | 2.00 |
| c3 | 70.8s | 32.9s | 27.7s | 128.5s | 2.77 |
| c4 | 64.6s | 39.2s | 29.7s | 101.2s | 2.80 |
| c5 | 31.3s | 43.9s | 36.3s | 115.7s | 3.26 |
| c6 | 44.7s | 52.6s | 43.0s | 127.1s | 3.43 |
| c7 | 2.9s | 56.7s | 42.2s | 114.0s | 3.20 |

两条规律：

1. **等待资源持续下降**（265s → 2.9s），说明并发确实在消除排队。
2. **单任务耗时先降后升**（326.9s → 101.2s 最低 → 127.1s）。c4 之后，
   OCR 阶段自身因资源争抢变慢（字幕定位 26.0s → 56.7s，+118%），
   抵消了排队减少的收益。

c4 的吞吐平台（2.80，几乎等于 c3 的 2.77）来自单任务耗时降到最低点（101.2s），
但等待资源仍剩 64.6s。c5 之后等待几乎消失，吞吐才继续上升。

### 12.3 与第一轮（4 任务）的差异

第一轮 c2 相对 c1 是 +31.9%，第二轮是 +53.5%。原因不是测量错误，
而是**任务数不同**：8 个任务时 c1 的排队更长（265s vs 115s），
并发消除的排队更多，所以相对收益更大。

**这说明"提升百分比"依赖于队列长度**，不是一个固定常数。
判断该设几，应看绝对吞吐和显存余量，而不是只看相对提升。
