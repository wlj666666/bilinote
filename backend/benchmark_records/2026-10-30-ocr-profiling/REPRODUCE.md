# 复现本轮 OCR 实验

在 `backend` 目录、同一个 bilinote 环境中运行，先确认没有其他 GPU 计算任务。
以下命令使用新的输出目录；现有数据不会覆盖。对应素材为报告中的本地 692.8 秒视频。
如果改用其他视频，需要重新选择字幕 ROI 和差异复查时间，不能照搬最后一条 review 命令。

```bash
export BILINOTE_PYTHON=/home/unbekannter/miniconda3/envs/bilinote/bin/python
campaign_dir=data/ocr_profile/replay
campaign_video=data/media/0f956892aa50b7f75b445f10/BV19mYE6ZEM8.mp4

"$BILINOTE_PYTHON" scripts/profile_ocr.py prepare "$campaign_video" \
  --dataset "$campaign_dir/frames" --count 96 --roi 0.10 0.79 0.90 0.92

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/baseline" \
  --label baseline --levels 1,2,3,4 --repeats 3

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/roi" \
  --label roi-baseline --crop roi --levels 1,2,3,4,6,8 --repeats 3

bash start-gpu.sh scripts/profile_ocr.py matrix \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/tuning" --repeats 2 \
  --variants no-cls,batch16,tf32,det960,roi-no-cls,det1280-effective,det960-effective,no-cls-tf32

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/profiles-full" \
  --label baseline --levels 1,2 --repeats 1 --profile --limit 12

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/profiles-shapes" \
  --label baseline --levels 1 --repeats 1 --profile --limit 12

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/profiles-shapes" \
  --label batch16 --rec-batch 16 --levels 1 --repeats 1 --profile --limit 12

bash start-gpu.sh scripts/profile_ocr.py run \
  --dataset "$campaign_dir/frames" --output "$campaign_dir/profiles-roi" \
  --label roi-baseline --crop roi --levels 1,4,8 --repeats 1 --profile --limit 48

bash start-gpu.sh scripts/profile_ocr.py video "$campaign_video" \
  --output "$campaign_dir/video/video-baseline.json"

bash start-gpu.sh scripts/profile_ocr.py video "$campaign_video" \
  --output "$campaign_dir/video/video-no-cls-tf32.json" --no-cls --tf32

bash start-gpu.sh scripts/profile_ocr.py video "$campaign_video" \
  --output "$campaign_dir/video/video-tf32.json" --tf32

bash start-gpu.sh scripts/profile_ocr.py review "$campaign_video" \
  --roi-report "$campaign_dir/video/video-baseline.json" \
  --times 59.4 112.8 269.4 364.2 365.6 439.4 654.2 675.0 684.4 \
  --output "$campaign_dir/quality-review/angles.json"

bash start-gpu.sh scripts/profile_ocr.py environment --output "$campaign_dir/environment.json"

"$BILINOTE_PYTHON" scripts/profile_ocr.py report --root "$campaign_dir" \
  --output "$campaign_dir/SUMMARY.md"
```

输出自动数值汇总、SVG 曲线、每图结果、业务阶段计时、ORT 原始 trace 及显存采样。
本轮完整原因解释与目视复查记录另见 `backend/OCR_PROFILING_REPORT_2026-10-30.md`。
表中的中位数需要对应的重复实验，不用开启 profiling 的少量样本耗时替代。
本轮首批 ROI 1/2/3/4 与后补 6/8 分两次执行，各自反转顺序；以上合并运行会改变试验顺序，
不会保证逐次耗时相同。判断趋势时应结合重复范围和实际设备状态。
