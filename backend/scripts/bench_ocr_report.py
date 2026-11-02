"""Assemble the OCR concurrency report from measured artifacts only.

Every number printed here is read from a result file that a benchmark run
produced; nothing is hard-coded or interpolated. If an artifact is missing the
section is skipped rather than filled with a placeholder.

Usage (from backend):
  python scripts/bench_ocr_report.py \
      --root data/ocr_bench_2026-10-31 --output OCR_CONCURRENCY_REPORT_2026-10-31.md
"""
import argparse
import json
from pathlib import Path
import statistics


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def collect_image_trials(directory, label):
    """Per-trial rows for a profile_ocr run directory."""
    rows = {}
    for trial in sorted(Path(directory).glob(f'{label}-c*-r*')):
        if (trial / 'excluded.json').exists():
            continue
        result = load(trial / 'result.json')
        if not result or result.get('status') != 'success':
            continue
        config = result['config']
        if config.get('profile'):
            continue
        rows.setdefault(config['concurrency'], []).append({
            'wall': result['wall_seconds'], 'frames': len(result['outputs']),
            'p95': result['latency_p95'], 'peak': result.get('gpu_peak_mib'),
            'cpu': result.get('mean_busy_cpu_cores'),
            'stages': result.get('stages') or {},
        })
    return rows


def table_image(rows, title):
    if not rows:
        return ''
    base = statistics.median(r['frames'] / r['wall'] for r in rows[min(rows)])
    lines = [f'### {title}', '',
             '| 并发 | 轮次 | 中位墙钟(s) | 中位吞吐(图/s) | 吞吐区间 | 相对c1 | 单图P95(ms) | 显存峰值(MiB) | CPU核 |',
             '|---:|---:|---:|---:|---|---:|---:|---:|---:|']
    for concurrency in sorted(rows):
        items = rows[concurrency]
        fps = [r['frames'] / r['wall'] for r in items]
        median_fps = statistics.median(fps)
        lines.append(
            f'| {concurrency} | {len(items)} | {statistics.median(r["wall"] for r in items):.3f} '
            f'| {median_fps:.2f} | {min(fps):.2f}–{max(fps):.2f} '
            f'| {(median_fps / base - 1) * 100:+.1f}% '
            f'| {statistics.median(r["p95"] for r in items) * 1000:.0f} '
            f'| {max((r["peak"] or 0) for r in items)} '
            f'| {statistics.median(r["cpu"] or 0 for r in items):.2f} |')
    return '\n'.join(lines) + '\n'


def stage_table(rows, title):
    """Per-image stage cost, which shows where the extra concurrency time goes."""
    if not rows:
        return ''
    stages = set()
    for items in rows.values():
        for row in items:
            stages.update(row['stages'])
    if not stages:
        return ''
    ordered = ['detect', 'det_run', 'cls_run', 'recognize', 'rec_run']
    ordered = [name for name in ordered if name in stages]
    if not ordered:
        return ''
    lines = [f'### {title}', '',
             '| 并发 | ' + ' | '.join(ordered) + ' |',
             '|---:|' + '---:|' * len(ordered)]
    for concurrency in sorted(rows):
        items = rows[concurrency]
        cells = []
        for name in ordered:
            per_image = []
            for row in items:
                stage = row['stages'].get(name)
                if stage and row['frames']:
                    per_image.append(stage['seconds'] / row['frames'] * 1000)
            cells.append(f'{statistics.median(per_image):.1f}' if per_image else '—')
        lines.append(f'| {concurrency} | ' + ' | '.join(cells) + ' |')
    lines.append('')
    lines.append('数值为每图平均毫秒，来自宿主侧阶段计时；多进程累计值不可相加当作墙钟时间。')
    return '\n'.join(lines) + '\n'


def operator_section(path):
    data = load(path)
    if not data:
        return ''
    lines = ['### 算子时间分布（ORT host node 事件，非 CUDA kernel 独占时间）', '']
    for worker, models in data.get('groups', {}).items():
        for model, info in models.items():
            top = info.get('top_operators') or []
            if not top:
                continue
            lines.append(f'**{worker} / {model}** — traces={info["traces"]}，'
                         f'provider={info["providers"]}')
            lines.append('')
            lines.append('| 算子 | 时间占比 | 事件数 | 宿主毫秒 |')
            lines.append('|---|---:|---:|---:|')
            for row in top[:6]:
                lines.append(f'| `{row["op"]}` | {row["share"]:.1%} | {row["events"]} '
                             f'| {row["host_ms"]:.1f} |')
            lines.append('')
    return '\n'.join(lines)


def e2e_table(root, _unused=''):
    """Collect every e2e-g*-summary.json directly under the run root."""
    summaries = sorted(Path(root).glob('*/e2e-g*-summary.json'))
    if not summaries:
        return ''
    lines = ['| 配置 | 轮次 | 中位总耗时(s) | 吞吐(任务/分钟) | 提交→完成P95(s) | 显存峰值(MiB) | GPU利用率均值 | 最大并行任务 | 成功/总 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for path in summaries:
        data = load(path)
        if not data:
            continue
        lines.append(f'| GPU={data["gpu_slots"]} / CPU={data["cpu_slots"]} | {data["repeats"]} '
                     f'| {data["elapsed_median"]:.1f} | {data["jobs_per_minute_median"]:.2f} '
                     f'| {data["completion_p95_median"]:.1f} | {data["gpu_peak_max"]:.0f} '
                     f'| {data.get("gpu_util_mean", 0):.1f}% | {data["max_running_max"]} '
                     f'| {data["succeeded_total"]}/{data["jobs_total"]} |')
    return '\n'.join(lines) + '\n' if len(lines) > 2 else ''


def wait_section(root):
    """Per-job stage medians, which show where time is actually spent."""
    names = ['e2e-g1c1', 'e2e-g2c2', 'e2e-g2c1']
    available = [name for name in names if (Path(root) / name).is_dir()]
    if not available:
        return ''
    rows = {}
    for name in available:
        jobs = []
        for path in sorted((Path(root) / name).glob('e2e-g*-r*.json')):
            data = load(path)
            if data:
                jobs.extend(data.get('jobs_detail') or [])
        rows[name] = jobs
    stages = ['WAITING_RESOURCE', 'DETECTING_SUBTITLES', 'EXTRACTING_SUBTITLES', 'SUMMARIZING']
    lines = ['| 配置 | 任务数 | ' + ' | '.join(stages) + ' |',
             '|---|---:|' + '---:|' * len(stages)]
    for name, jobs in rows.items():
        if not jobs:
            continue
        label = name.replace('e2e-', '').replace('g', 'GPU=').replace('c', ' / CPU=')
        cells = []
        for stage in stages:
            values = [j['stage_times'][stage] for j in jobs if stage in j['stage_times']]
            cells.append(f'{statistics.median(values):.1f}' if values else '—')
        lines.append(f'| {label} | {len(jobs)} | ' + ' | '.join(cells) + ' |')
    lines.append('')
    lines.append('数值为单任务该阶段耗时中位数（秒）。`WAITING_RESOURCE` 是任务已领取但排队等资源的时间，'
                 '它直接决定用户等待，是判断并发是否真正生效的关键指标。')
    return '\n'.join(lines) + '\n'


def e2e_detail_section(root):
    """Per-video detail, so a slow outlier cannot hide inside a median."""
    lines = []
    for name in ['e2e-g1c1', 'e2e-g2c2', 'e2e-g2c1']:
        directory = Path(root) / name
        if not directory.is_dir():
            continue
        jobs = []
        for path in sorted(directory.glob('e2e-g*-r*.json')):
            data = load(path)
            if data:
                jobs.extend(data.get('jobs_detail') or [])
        if not jobs:
            continue
        label = name.replace('e2e-', '').replace('g', 'GPU=').replace('c', ' / CPU=')
        lines.append(f'**{label}**')
        lines.append('')
        lines.append('| 视频 | 队列等待(s) | 总耗时(s) | 状态 |')
        lines.append('|---|---:|---:|---|')
        by_video = {}
        for job in jobs:
            by_video.setdefault(job['video'], []).append(job)
        for video, items in sorted(by_video.items()):
            lines.append(f'| {video} | {statistics.median(j["queue_seconds"] for j in items):.1f} '
                         f'| {statistics.median(j["total_seconds"] for j in items):.1f} '
                         f'| {items[0]["state"]} |')
        lines.append('')
    return '\n'.join(lines)


def consistency_section(path):
    data = load(path)
    if not data:
        return ''
    verdict = data['verdict']
    lines = ['| 指标 | 结果 |', '|---|---|',
             f'| 基线自身多轮一致 | {verdict["baseline_self_consistent"]} |',
             f'| 全部视频文本完全一致 | {verdict["all_videos_text_identical"]} |',
             f'| 全部视频分段时间/文本三元组一致 | {verdict["all_videos_segments_identical"]} |',
             f'| 最低文本相似度 | {verdict["min_text_similarity"]} |', '']
    lines.append('| 视频 | 相似度最低 | 文本一致 | 三元组一致 | 变化片段数上限 |')
    lines.append('|---|---:|---|---|---:|')
    for video, row in data['comparisons'].items():
        lines.append(f'| {video} | {row["text_similarity_min"]:.6f} | '
                     f'{row["all_text_identical"]} | {row["all_segments_identical"]} | '
                     f'{row["segments_changed_max"]} |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()

    roi = collect_image_trials(root / 'roi-r1', 'roi-baseline')
    full = collect_image_trials(root / 'full-r1', 'baseline')

    environment = load(root / 'environment.json') or {}
    sections = []

    sections.append('# OCR 并发完整验证报告（2026-10-31）\n')
    sections.append('本报告所有数字均来自本轮实际运行产生的 `result.json`，'
                    '未复用任何历史结论，也未做插值。原始数据见 `backend/data/ocr_bench_2026-10-31/`。\n')

    if environment:
        gpu = environment.get('gpu')
        sections.append('## 1. 环境\n')
        sections.append(f'- GPU：{gpu}' if gpu else '')
        sections.append(f'- Python：{environment.get("python", "")}')
        sections.append(f'- CPU 逻辑核：{environment.get("cpu_count")}')
        sections.append(f'- 可用 provider：{environment.get("available_providers")}')
        sections.append('')

    sections.append('## 2. 图片级并发（性能对照，profiling 关闭）\n')
    sections.append('并发 N 表示 N 个独立进程，每进程一套 OCR 引擎与独立 CUDA context，'
                    '总图片数固定。模型加载、预热与读图不计入稳态耗时。\n')
    sections.append(table_image(roi, '字幕 ROI 裁剪（生产提取阶段负载）'))
    sections.append(table_image(full, '1280 宽全画面（生产定位扫描阶段负载）'))
    sections.append(stage_table(roi, 'ROI 各阶段每图耗时'))
    sections.append(stage_table(full, '全画面各阶段每图耗时'))

    operator = operator_section(root / 'operator-attribution-roi.json')
    if operator:
        sections.append('## 3. 算子时间归因\n')
        sections.append(operator)
        sections.append('')

    e2e = e2e_table(root)
    sections.append('## 4. 视频级端到端（真实调度器 + 真实 OCR 全流程）\n')
    sections.append('每轮使用隔离数据库与临时目录，包含解码、定位扫描、ROI 提取、'
                    '视觉复用、跨帧合并、局部复查与质量门禁；总结接口以固定延时替代，'
                    '不调用付费 API。4 个视频分别为 60/120/180/300 秒，'
                    '覆盖 768×480、1920×1080、1080×1920 三种分辨率。\n')
    sections.append(e2e or '_（端到端实验数据缺失）_\n')
    wait = wait_section(root)
    if wait:
        sections.append('### 阶段耗时分解\n')
        sections.append(wait)
        sections.append('')
    detail = e2e_detail_section(root)
    if detail:
        sections.append('### 逐视频结果\n')
        sections.append(detail)
        sections.append('')

    consistency = consistency_section(root / 'consistency-g2c2.json')
    if consistency:
        sections.append('## 5. 输出一致性\n')
        sections.append('比较相同视频在 GPU=1/CPU=1 与 GPU=2/CPU=2 下产出的字幕，'
                        '先确认基线自身多轮是否稳定，再判断差异能否归因于并发。\n')
        sections.append(consistency)

    body = '\n'.join(line for line in sections if line is not None)
    Path(args.output).write_text(body, encoding='utf-8')
    print(f'written: {Path(args.output).resolve()}')


if __name__ == '__main__':
    main()
