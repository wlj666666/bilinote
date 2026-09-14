# 本地画面字幕提取

表单和浏览器插件提供“语音转写 / 画面字幕 OCR”。两条路线共同优先使用平台字幕（包括插件预取），没有可用平台字幕时才执行所选路线。旧客户端未传参数时使用 `asr`。

请求增加：

```json
{"text_extraction_method": "ocr"}
```

OCR 成功后返回原有 `TranscriptResult`：`language`、`full_text`、`segments[{start,end,text}]`；实际来源放在 `raw.source`。总结、原文时间轴及问答保持现有输入结构。OCR 本身不向总结模型附加图片；另行开启原有“截图”或“视频理解”选项时，仍按原逻辑生成并附加图片。

## 安装

在运行后端的 Python 环境中安装基础依赖，再安装：

```bash
python -m pip install -r requirements-ocr.txt
```

固定的 RapidOCR wheel 包含默认 PP-OCRv6 检测/识别和方向分类 ONNX 模型。首次初始化将其复制到 `models/ocr/`，推理不需要网络或大模型 API。`OCR_DEVICE=cpu` 使用 CPU，`OCR_DEVICE=cuda` 使用 NVIDIA GPU；直接运行 Python 默认 CPU，`start-gpu.sh` 默认设置 CUDA。`OCR_THREADS` 默认 1。FFmpeg 仍需正常安装。OCR 在独立子进程中运行，解码和 OpenCV 使用单线程；连续 120 秒无进度会停止并返回失败（`OCR_STALL_TIMEOUT_SECONDS` 可调整），避免原生库卡住主任务。检测长边限制为 1280，避免将窄字幕条无谓放大；识别使用原图中的文字裁剪。

### NVIDIA GPU 环境

先安装基础依赖，再在同一个后端 Python 环境执行：

```bash
python -m pip uninstall -y onnxruntime
python -m pip install -r requirements-ocr-gpu.txt
bash start-gpu.sh
```

CPU 和 GPU 两个 ONNX Runtime 分发包共享 Python 导入路径，不要同时安装。以后重新安装基础 `requirements.txt` 时，也需要重新完成上述替换。GPU 依赖固定为 ONNX Runtime 1.21.0、CUDA 12.8 系列和 cuDNN 9.8，并沿用语音转写环境已有的 cuBLAS/cuDNN 版本；仍需兼容的 NVIDIA 驱动。参见 [ONNX Runtime 官方 CUDA 兼容说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)。

定位和提取共用 GPU OCR 引擎，三个实际模型会话都必须以 `CUDAExecutionProvider` 为首选，否则任务明确报错；不静默回退 CPU。视频解码、裁剪和字幕合并仍在 CPU 执行。诊断及原文 `raw.runtime` 保存实际设备和各模型 providers。切换设备会使原文缓存失效，避免将旧 CPU 缓存当作 GPU 实测。

需要主动使用 CPU 时，可用 `OCR_DEVICE=cpu bash start-gpu.sh`，语音转写的 GPU 配置不受影响。

实测设备、CPU/GPU 耗时和结果对照见 [OCR_GPU_VALIDATION.md](OCR_GPU_VALIDATION.md)。

当前验证范围为 Python 源码部署。PyInstaller 等独立可执行文件需要额外处理 OCR 子进程入口及依赖打包，不属于本次验证范围。

## 数据流

1. 校验输入及路线对应的原文缓存。
2. 获取平台字幕。
3. 按需获取视频，OCR 不下载或转写音频。
4. 全片每 2 秒做一次文字检测，以 20 秒窗口分析位置、排版、内容变化及水平运动，选择字幕候选；相近的双行候选可合并，竞争区域标记为不确定。先后出现的不同位置会按时间拆分；尾段长期不变的字幕可由此前位置和多数探测帧共同确认；若随后转为片尾画面，也接受首个探测帧对此前已稳定定位区域的确认。
5. 对候选区域顺序扫描，默认每秒采样 5 次（`OCR_FPS` 可设 1–12）。保留视频实际时间戳，不使用拼图或旧抽帧模块的 1000 帧限制。
6. 合并连续相同字幕，保留间隔后的重复句子及数字、否定词变化，检查扫描覆盖、候选缺失和低分比例。
7. 通过检查后保存原文，再调用原有总结流程。失败或不确定时不自动切换语音、不调用总结模型。

缓存包含输入身份、分 P、路线、模型/算法版本和采样参数。切换路线在网页端创建新任务；历史原文不会被覆盖。旧缓存缺少身份标记时重新提取。原文来源包括 `platform_subtitle`、`client_prefetched`、`asr`、`hard_subtitle_ocr`。

## 验证本地视频

不访问平台、不执行 Whisper、不调用总结模型：

```bash
python scripts/check_hard_subtitles.py /path/to/video.mp4 --output /tmp/ocr-check.json
```

诊断文件包含各窗口的候选区域、示例文字、评分、扫描信息和提取文本。线上任务保存到 `note_results/<task_id>_ocr_diagnostics.json`。质量存疑时保留部分文本供排查，但不发送给总结模型。

```bash
PYTHONPATH=. python -m pytest tests/test_hard_subtitles.py tests/test_text_extraction_routes.py -q
```

详细测试范围见 [OCR_VALIDATION.md](OCR_VALIDATION.md)。

## 当前边界

面向清晰、中英文、位置相对稳定的一到两行字幕。长度变化和分段位置变化通过跟踪处理；静态正文和水印通过时间特征降权。多处动态文字、滚动字幕、模糊小字、短暂花字仍可能误判或漏识别。

“正常扫描完成”不等于字幕百分之百完整，也不证明所有讲话都配有字幕。OCR 分数表示识字稳定性，不是字幕区域选择正确的概率。界面显示失败原因，用户可以重试或主动改用语音转写；没有框选要求。

`unlocated_seconds` 记录未可靠定位区域的时长。短缺口可能是静默或转场；显著缺口/低分会拒绝生成。需要高完整性的场景仍应核对原文及诊断。
