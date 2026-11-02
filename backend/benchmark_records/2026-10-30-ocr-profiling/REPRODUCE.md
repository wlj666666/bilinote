# OCR profiling 复现步骤

在仓库根目录中运行。请使用自己有权处理的本地测试视频，并确认没有其他 GPU 任务。
以下示例把中间数据写入独立目录；如需重复运行，请先更换输出目录或备份已有结果。

```bash
cd backend
export BILINOTE_PYTHON="${BILINOTE_PYTHON:-python}"
campaign_dir=data/ocr_profile/replay
campaign_video=/path/to/authorized-sample.mp4

"$BILINOTE_PYTHON" scripts/profile_ocr.py prepare "$campaign_video" \
  --dataset "$campaign_dir/frames" --count 96

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/baseline" \
  --label baseline --levels 1,2,3,4 --repeats 3

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/roi" \
  --label roi-baseline --crop roi --levels 1,2,3,4,6,8 --repeats 3

bash start-gpu.sh scripts/profile_ocr.py matrix \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/tuning" --repeats 2 \
  --variants no-cls,batch16,tf32,det960,roi-no-cls,det1280-effective,det960-effective,no-cls-tf32

bash start-gpu.sh scripts/profile_ocr.py environment \
  --output "$campaign_dir/environment.json"

"$BILINOTE_PYTHON" scripts/profile_ocr.py report --root "$campaign_dir" \
  --output "$campaign_dir/SUMMARY.md"
```

使用自己的素材时，应按其字幕位置重新选择 ROI；不同视频、设备及运行顺序会影响结果。
性能比较应使用重复实验的汇总值，不要用开启 profiling 的少量样本代替。
原始帧、逐次输出和 profiling trace 可能包含素材内容及本机路径，请仅保存在本地；
公开分享时只保留经检查的汇总指标。
