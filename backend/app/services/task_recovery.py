"""Reconcile persisted state after the in-memory worker queue has been lost."""
import json
from pathlib import Path


def recover_interrupted_tasks(output_dir: str | Path) -> int:
    recovered = 0
    for path in Path(output_dir).glob("*.status.json"):
        task_id = path.name.removesuffix(".status.json")
        if task_id.endswith("_markdown"):
            continue  # Legacy summary status files were never real tasks.
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("status") in {"SUCCESS", "FAILED"}:
                continue
            result_path = path.with_name(f"{task_id}.json")
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            complete = bool(result.get("markdown") and result.get("audio_meta") and result.get("transcript"))
        except (OSError, ValueError, TypeError, AttributeError):
            complete = False
        state = {"status": "SUCCESS"} if complete else {
            "status": "FAILED",
            "message": "服务已重启，上次任务已中断。请点击重试；已保留字幕和媒体缓存。",
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        recovered += 1
    return recovered
