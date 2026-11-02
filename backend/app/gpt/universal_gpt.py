from app.gpt.base import GPT
from app.gpt.prompt_builder import generate_base_prompt
from app.models.gpt_model import GPTSource
import os
import hashlib
import json
import time
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from app.gpt.prompt import BASE_PROMPT, AI_SUM, SCREENSHOT, LINK, MERGE_PROMPT
from app.gpt.utils import fix_markdown
from app.gpt.request_chunker import RequestChunker
from app.gpt.timestamp_markers import (
    SOURCE_TIME_PROMPT, TIMESTAMP_WARNING, marker_seconds, normalize_timestamp_markers,
    resolve_timestamp_markers, source_seconds, timestamp_marker_count,
)
from app.models.transcriber_model import TranscriptSegment
from app.utils.timestamps import format_timestamp
from typing import List
from app.services.storage_scope import scoped_notes_dir

logger = logging.getLogger(__name__)


class UniversalGPT(GPT):
    def __init__(self, client, model: str, temperature: float = 0.7):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.screenshot = False
        self.link = False
        self.max_request_bytes = int(os.getenv("OPENAI_MAX_REQUEST_BYTES", str(8 * 1024 * 1024)))
        self.checkpoint_dir = scoped_notes_dir(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 初始化时缓存重试配置，避免每次请求重复读取环境变量
        self._max_retry_attempts = max(1, int(os.getenv("OPENAI_RETRY_ATTEMPTS", "2")))
        self._retry_base_backoff = float(os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "1.5"))

    def _format_time(self, seconds: float) -> str:
        return format_timestamp(seconds)

    def _build_segment_text(self, segments: List[TranscriptSegment]) -> str:
        return "\n".join(
            f"{self._format_time(seg.start)} [t={max(0, int(seg.start))}] - {seg.text.strip()}"
            for seg in segments
        )

    def ensure_segments_type(self, segments) -> List[TranscriptSegment]:
        return [TranscriptSegment(**seg) if isinstance(seg, dict) else seg for seg in segments]

    def create_messages(self, segments: List[TranscriptSegment], **kwargs):

        content_text = generate_base_prompt(
            title=kwargs.get('title'),
            segment_text=self._build_segment_text(segments),
            tags=kwargs.get('tags'),
            _format=kwargs.get('_format'),
            style=kwargs.get('style'),
            extras=kwargs.get('extras'),
        )
        content_text += '\n' + SOURCE_TIME_PROMPT

        video_img_urls = kwargs.get('video_img_urls', [])

        content: list[dict] | str
        if video_img_urls:
            # 有截图时走 OpenAI 多模态 content 数组（text + image_url）
            content = [{"type": "text", "text": content_text}]
            for url in video_img_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": "auto"
                    }
                })
        else:
            # 纯文本场景退回 string content：DeepSeek deepseek-chat 等非多模态模型
            # 不识别 [{"type":"text",...}] 数组形态，会返回 invalid_request_error
            # （issue #282）。OpenAI 规范本身也允许 content 为 string。
            content = content_text

        messages = [{
            "role": "user",
            "content": content
        }]

        return messages

    def list_models(self):
        return self.client.models.list()

    def _estimate_messages_bytes(self, messages: list) -> int:
        import json
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _build_merge_messages(self, partials: list) -> list:
        merge_text = MERGE_PROMPT + '\n' + SOURCE_TIME_PROMPT + "\n\n" + "\n\n---\n\n".join(partials)
        # 合并阶段没有图片，直接用 string content 兼容非多模态模型（issue #282）
        return [{
            "role": "user",
            "content": merge_text
        }]

    def _checkpoint_path(self, checkpoint_key: str) -> Path:
        safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in checkpoint_key)
        return self.checkpoint_dir / f"{safe_key}.gpt.checkpoint.json"

    def _build_source_signature(self, source: GPTSource) -> str:
        from app.services.task_context import summary_revision
        payload = {
            "timestamp_format": "source-intervals-v3",
            "model": self.model,
            "provider_endpoint": str(getattr(self.client, "base_url", "")),
            "generation_revision": summary_revision.get(),
            "temperature": self.temperature,
            "max_request_bytes": self.max_request_bytes,
            "title": source.title,
            "tags": source.tags,
            "format": source._format,
            "style": source.style,
            "extras": source.extras,
            "video_img_urls": source.video_img_urls or [],
            "segments": [
                {
                    "start": getattr(seg, "start", None),
                    "end": getattr(seg, "end", None),
                    "text": getattr(seg, "text", "")
                }
                for seg in source.segment
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_checkpoint(self, checkpoint_key: str, source_signature: str) -> dict | None:
        path = self._checkpoint_path(checkpoint_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("source_signature") != source_signature:
                path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            path.unlink(missing_ok=True)
            return None

    def _save_checkpoint(self, checkpoint_key: str, source_signature: str, partials: list, phase: str) -> None:
        path = self._checkpoint_path(checkpoint_key)
        data = {
            "version": 1,
            "source_signature": source_signature,
            "phase": phase,
            "partials": partials,
            "timestamp_warning": getattr(self, '_timestamp_warning', False),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _clear_checkpoint(self, checkpoint_key: str) -> None:
        self._checkpoint_path(checkpoint_key).unlink(missing_ok=True)

    @staticmethod
    def _is_insufficient_quota_error(exc: Exception) -> bool:
        raw = str(exc)
        return (
            "insufficient_user_quota" in raw
            or "预扣费额度失败" in raw
            or "insufficient quota" in raw.lower()
        )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        raw = str(exc).lower()
        if UniversalGPT._is_insufficient_quota_error(exc):
            return False
        retryable_tokens = (
            "error code: 524",
            "bad_response_status_code",
            "timed out",
            "timeout",
            "rate limit",
            "error code: 429",
            "error code: 500",
            "error code: 502",
            "error code: 503",
            "error code: 504",
            "apiconnectionerror",
            "connection error",
            "service unavailable",
        )
        if any(token in raw for token in retryable_tokens):
            return True

        status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status_code", None)
        return status in {408, 409, 429, 500, 502, 503, 504, 524}

    @staticmethod
    def _is_temperature_unsupported_error(exc: Exception) -> bool:
        """OpenAI o1/o3/gpt-5 系列等新模型不接受自定义 temperature，
        只允许默认值 1，传 0.7 会报 `'temperature' does not support 0.7 ...`。"""
        raw = str(exc).lower()
        return "temperature" in raw and (
            "does not support" in raw
            or "unsupported_value" in raw
            or "only the default" in raw
        )

    def _do_create(self, messages: list):
        from app.services.task_resources import resources, provider_cooldown_key, provider_cooldown_remaining, record_provider_cooldown
        from app.services.task_context import status, retry_wait
        key = provider_cooldown_key(self.client)
        while True:
            remaining = provider_cooldown_remaining(key)
            if remaining > 0:
                retry_wait(min(remaining, 10), '模型接口限流，等待恢复后重试')
                continue
            with resources('api'):
                if provider_cooldown_remaining(key) > 0:
                    continue
                status('SUMMARIZING', '正在请求模型总结')
                try:
                    return self._do_create_unlimited(messages)
                except Exception as exc:
                    headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
                    raw_retry = headers.get('retry-after')
                    seconds = None
                    if raw_retry:
                        try:
                            seconds = float(raw_retry)
                        except ValueError:
                            try:
                                from email.utils import parsedate_to_datetime
                                seconds = parsedate_to_datetime(raw_retry).timestamp() - time.time()
                            except (ValueError, TypeError, OverflowError):
                                pass
                    import math
                    code = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
                    if code == 429 and not self._is_insufficient_quota_error(exc) and seconds is None:
                        seconds = max(1, self._retry_base_backoff)
                    if seconds is not None and math.isfinite(seconds) and seconds > 0:
                        record_provider_cooldown(key, seconds)
                    raise

    def _do_create_unlimited(self, messages: list):
        """单次调用。如果模型拒绝自定义 temperature，就地去掉该参数再试一次
        （不消耗外层的重试次数预算），仍失败则把异常抛给外层重试逻辑。"""
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
            )
        except Exception as exc:
            if self._is_temperature_unsupported_error(exc):
                print(f"[universal_gpt] 模型 {self.model} 不支持自定义 temperature，改用默认值重试")
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
            raise

    def _chat_completion_create(self, messages: list):
        last_exc = None
        for attempt in range(self._max_retry_attempts):
            started = time.monotonic()
            logger.info("模型请求 model=%s attempt=%s/%s bytes=%s", self.model,
                        attempt + 1, self._max_retry_attempts, self._estimate_messages_bytes(messages))
            try:
                response = self._do_create(messages)
                logger.info("模型请求完成 model=%s elapsed=%.1fs", self.model, time.monotonic() - started)
                return response
            except Exception as exc:
                last_exc = exc
                logger.warning("模型请求失败 model=%s attempt=%s elapsed=%.1fs error=%s",
                               self.model, attempt + 1, time.monotonic() - started, type(exc).__name__)
                if attempt == self._max_retry_attempts - 1 or not self._is_retryable_error(exc):
                    raise
                sleep_seconds = self._retry_base_backoff * (2 ** attempt)
                from app.services.task_context import retry_wait
                retry_wait(sleep_seconds)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("chat completion failed without exception")

    def _timestamp_repair_messages(self, messages: list, markdown: str, error: ValueError,
                                   allowed_seconds: set[int]) -> list:
        invalid = re.search(r't=(\d{1,20})(?!\d)', str(error))
        target = int(invalid.group(1)) if invalid else 0
        nearby = sorted(sorted(allowed_seconds, key=lambda value: abs(value - target))[:6])
        feedback = (
            f'时间标记校验失败：{error}。\n'
            f'附近可核对的字幕编号：{", ".join(f"[t={value}]" for value in nearby) or "无"}。\n'
            '请只修正上一份笔记的原片跳转或截图时间标记，保留正文和标题。'
            '必须从原材料逐字复制与内容对应的编号，附近编号仅供核对，不得随意替换。'
            '无法确认的标记请删除，不得编造时间。仅输出修正后的完整 Markdown。'
        )
        return [*messages, {'role': 'assistant', 'content': markdown}, {'role': 'user', 'content': feedback}]

    def _request_markdown(self, messages: list, allowed_seconds: set[int], require_content: bool = False,
                          segments=()) -> str:
        # Keep source IDs in checkpoints/merges; format them only at the end.
        # An optional link must not discard a successfully generated note.
        fallback_markdown = None
        request_messages = messages
        for attempt in range(2):
            try:
                response = self._chat_completion_create(request_messages)
            except Exception:
                if fallback_markdown is None:
                    raise
                logger.warning('时间标记修复请求失败，保留原笔记并移除无效标记', exc_info=True)
                break
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                if fallback_markdown is None:
                    raise ValueError('模型未返回有效笔记正文')
                break
            markdown = content.strip()
            if fallback_markdown is None:
                fallback_markdown = markdown
            try:
                normalized = normalize_timestamp_markers(markdown, allowed_seconds, segments)
                if require_content and 'Content-[t=' not in normalized:
                    raise ValueError('模型未返回原片跳转时间编号')
                if normalized != markdown:
                    logger.info('已根据字幕区间纠正时间编号')
                if attempt and timestamp_marker_count(normalized) < timestamp_marker_count(fallback_markdown):
                    self._timestamp_warning = True
                return normalized
            except ValueError as exc:
                if attempt:
                    break
                request_messages = self._timestamp_repair_messages(messages, markdown, exc, allowed_seconds)
                if self._estimate_messages_bytes(request_messages) > self.max_request_bytes:
                    logger.warning('时间标记修复请求超过大小预算，保留原笔记并移除无效标记')
                    break
                logger.warning('模型时间标记无效，反馈具体错误后修复一次：%s', exc)

        markdown = normalize_timestamp_markers(fallback_markdown, allowed_seconds, segments, drop_invalid=True).strip()
        if not markdown:
            raise ValueError('移除无效时间标记后没有有效笔记正文')
        self._timestamp_warning = True
        logger.warning('部分原片跳转或截图未能生成，已保留笔记正文')
        return markdown

    def _merge_partials(self, partials: list, checkpoint_key: str | None, source_signature: str | None) -> str:
        def build_messages(texts, *_args, **_kwargs):
            return self._build_merge_messages(texts)

        merge_chunker = RequestChunker(
            lambda *_args, **_kwargs: [],
            self.max_request_bytes,
            self._estimate_messages_bytes
        )

        current_partials = list(partials)
        while len(current_partials) > 1:
            groups = merge_chunker.group_texts_by_budget(current_partials, build_messages)
            new_partials = []
            for group_idx, group in enumerate(groups):
                messages = build_messages(group)
                try:
                    group_text = '\n'.join(group)
                    markdown = self._request_markdown(
                        messages, marker_seconds(group_text), require_content='Content-[t=' in group_text,
                    )
                except Exception as exc:
                    if checkpoint_key and source_signature:
                        self._save_checkpoint(checkpoint_key, source_signature, current_partials, "merge")
                    raise

                new_partials.append(markdown)

                if checkpoint_key and source_signature:
                    remaining_partials = []
                    for remaining_group in groups[group_idx + 1:]:
                        remaining_partials.extend(remaining_group)
                    resumable_partials = new_partials + remaining_partials
                    self._save_checkpoint(checkpoint_key, source_signature, resumable_partials, "merge")

            current_partials = new_partials

        return current_partials[0]

    def summarize(self, source: GPTSource) -> str:
        self._timestamp_warning = False
        self.screenshot = source.screenshot
        self.link = source.link
        source.segment = self.ensure_segments_type(source.segment)
        checkpoint_key = source.checkpoint_key
        source_signature = self._build_source_signature(source) if checkpoint_key else None

        def message_builder(segments, image_urls, **kwargs):
            return self.create_messages(segments, video_img_urls=image_urls, **kwargs)

        chunker = RequestChunker(message_builder, self.max_request_bytes, self._estimate_messages_bytes)

        try:
            chunks = chunker.chunk(
                source.segment,
                source.video_img_urls or [],
                title=source.title,
                tags=source.tags,
                _format=source._format,
                style=source.style,
                extras=source.extras
            )
        except ValueError:
            chunks = chunker.chunk(
                source.segment,
                [],
                title=source.title,
                tags=source.tags,
                _format=source._format,
                style=source.style,
                extras=source.extras
            )

        partials = []
        checkpoint_phase = None
        if checkpoint_key and source_signature:
            checkpoint = self._load_checkpoint(checkpoint_key, source_signature)
            if checkpoint and isinstance(checkpoint.get("partials"), list):
                partials = checkpoint["partials"]
                checkpoint_phase = checkpoint.get('phase')
                self._timestamp_warning = bool(checkpoint.get('timestamp_warning'))

        if checkpoint_phase != 'merge' and len(partials) > len(chunks):
            partials = []
            self._timestamp_warning = False

        # Merge checkpoints already cover every source chunk. Their partial
        # count shrinks during merging and is not a count of summarized chunks.
        remaining_chunks = [] if checkpoint_phase == 'merge' else chunks[len(partials):]
        for chunk in remaining_chunks:
            messages = self.create_messages(
                chunk.segments,
                title=source.title,
                tags=source.tags,
                video_img_urls=chunk.image_urls,
                _format=source._format,
                style=source.style,
                extras=source.extras
            )
            try:
                markdown = self._request_markdown(
                    messages, source_seconds(chunk.segments),
                    require_content=bool(chunk.segments) and (source.link or 'link' in (source._format or [])),
                    segments=chunk.segments,
                )
            except Exception as exc:
                if checkpoint_key and source_signature:
                    self._save_checkpoint(checkpoint_key, source_signature, partials, "summarize")
                raise

            partials.append(markdown)
            if checkpoint_key and source_signature:
                self._save_checkpoint(checkpoint_key, source_signature, partials, "summarize")

        if len(partials) == 1:
            markdown = resolve_timestamp_markers(partials[0], source_seconds(source.segment))
            if checkpoint_key:
                self._clear_checkpoint(checkpoint_key)
            return f'{TIMESTAMP_WARNING}\n\n{markdown}' if self._timestamp_warning else markdown
        merged = self._merge_partials(partials, checkpoint_key, source_signature)
        markdown = resolve_timestamp_markers(merged, source_seconds(source.segment))
        if checkpoint_key:
            self._clear_checkpoint(checkpoint_key)
        return f'{TIMESTAMP_WARNING}\n\n{markdown}' if self._timestamp_warning else markdown
