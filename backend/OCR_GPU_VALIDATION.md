# OCR GPU 加速验证

环境：RTX 5060 Laptop GPU（约 8 GB），ONNX Runtime GPU 1.21.0，RapidOCR 3.9.2，CUDA 12.8 系列、cuDNN 9.8。保留原有语音转写所用 cuBLAS/cuDNN 版本。

## 相同视频的 CPU / GPU 对照

从本地 `BV1CuNd6GECx.mp4` 截取前 30 秒，依次运行相同版本代码和默认 5 fps 采样；仅切换 `OCR_DEVICE`。包含模型初始化、解码和子进程开销，不调用语音转写或总结模型。

| 阶段 | CPU | GPU | 加速比 |
| --- | ---: | ---: | ---: |
| 定位字幕（含初始化） | 65.630 秒 | 10.942 秒 | 约 6.0 倍 |
| 提取字幕 | 46.237 秒 | 7.427 秒 | 约 6.2 倍 |
| 合计 | 111.867 秒 | 18.369 秒 | 约 6.1 倍 |

两个结果均为 `success`，均为 19 条片段，完整文本及每条片段的文本、起止时间完全相同。这是单个样本的结果，不代表所有视频都能获得相同加速比或逐字准确率。

- [CPU 诊断与计时](data/ocr_validation/gpu_benchmark_cpu.json)
- [GPU 诊断与计时](data/ocr_validation/gpu_benchmark_cuda.json)

## 实际设备验证

不只检查 `get_available_providers()`，还检查三个模型的实际 session，并关闭 ONNX Runtime 的自动 CPU 重建回退。GPU 不可用时任务明确失败。

通过 ONNX Runtime profiling 跑实际文字图片，检测、方向分类、识别分别记录到 190、179、181 个 CUDA 节点执行事件。识别中另有 2 个 CPU 节点事件：部分形状等辅助运算在 CPU 上执行是正常行为，并不代表 OCR 模型整体回退到 CPU。

保持 FP32，关闭 TF32；使用 HEURISTIC 卷积算法选择并限制大工作区，避免字幕宽度变化导致反复穷举算法。

## 完整视频与回归

使用实际 `start-gpu.sh` 启动，完整处理 409.4 秒、1080×1920 的原视频：

- 定位 139.376 秒、提取 102.246 秒，总计 241.622 秒（约 4 分 2 秒）。
- `success`，261 条字幕；21 个窗口均成功定位，没有竞争区域或未定位时段。
- 末句“就OK了好吧”结束于 401.8 秒，后续 7.6 秒为片尾无讲话字幕画面。该空白在整条视频中未超过质量阈值。
- 保留最终的边界过滤和尾段延续修正，原文中未出现孤立 ASCII 单字符图案噪声；这不构成对所有视频的准确率保证。
- [完整 GPU 诊断与原文](data/ocr_validation/full_scan_cuda.json)

后端完整回归：124 项测试、8 项子测试通过。包括 GPU 缺失时明确失败、逐个核对实际模型 providers、禁用运行时 CPU 回退、CPU 路线、切换设备失效 OCR 缓存。实际会话参数也核对为 `use_tf32=0`、`HEURISTIC`、`cudnn_conv_use_max_workspace=0`。

替换后只安装了 `onnxruntime-gpu`，没有并存 CPU 分发包。语音转写使用的 cuBLAS 12.8.4.1 和 cuDNN 9.8.0.87 未改变，CTranslate2 的实际 CUDA 计算类型查询通过。本次未重新执行完整 Whisper 转写，也未调用付费总结模型。

## 使用方式

安装依赖后沿用 `bash start-gpu.sh`，默认启用 OCR CUDA；源码直接启动默认 CPU。两个设备共用同一套字幕定位、过滤和合并逻辑，前端流程不变。具体安装步骤见 [OCR.md](OCR.md)。

可复现的分阶段计时命令（在 backend 目录运行）：

```bash
python scripts/check_hard_subtitles.py VIDEO --device cpu --output /tmp/ocr-cpu.json
python scripts/check_hard_subtitles.py VIDEO --device cuda --output /tmp/ocr-cuda.json
```

计时和真实 providers 写入诊断文件；OCR 设备加入原文缓存签名，切换设备时不会误用另一设备的旧缓存。
