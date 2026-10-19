"""Private, per-user source caches, independent of a note task's lifetime."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

from app.models.audio_model import AudioDownloadResult
from app.services.task_process import FileLock
from app.utils.logger import get_logger
from app.utils.url_parser import extract_video_id, normalize_video_url

logger = get_logger(__name__)
RUNTIME_ROOT = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                else Path(__file__).resolve().parents[2] / 'data')


def source_identity(url: str, platform: str) -> dict:
    url = str(url)
    if platform == 'bilibili' and 'b23.tv' not in url:
        url = normalize_video_url(url)
        if url.endswith('?p=1'):
            url = url[:-4]
    elif platform in {'youtube', 'douyin'}:
        video_id = extract_video_id(url, platform)
        if video_id:
            url = (f'https://www.youtube.com/watch?v={video_id}' if platform == 'youtube'
                   else f'https://www.douyin.com/video/{video_id}')
    parsed = urlsplit(url)
    return {'url': urlunsplit(parsed._replace(fragment='')), 'platform': platform}


def cache_signature(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def quality_value(quality) -> str:
    return str(getattr(quality, 'value', quality))


class SourceCache:
    def __init__(self, user_id: int, input_key: str):
        self.user_id, self.input_key = int(user_id), input_key
        if self.user_id < 1 or len(input_key) != 64 or any(c not in '0123456789abcdef' for c in input_key):
            raise ValueError('无效的共享缓存标识')
        self.directory = Path(os.getenv('SOURCE_CACHE_DIR', str(RUNTIME_ROOT / 'source_cache'))).resolve() / 'users' / str(user_id) / input_key
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, kind: str):
        name = hashlib.sha256(kind.encode()).hexdigest()
        lock = FileLock(self.directory / f'{name}.lock')
        try:
            announced = False
            while not lock.acquire():
                if not announced:
                    from app.services.task_context import status
                    status('WAITING_RESOURCE', '等待同一视频的缓存处理完成')
                    announced = True
                time.sleep(.1)
            yield
        finally:
            lock.close()

    @staticmethod
    def read_json(path):
        try:
            data = Path(path).read_text(encoding='utf-8')
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_json(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f'.{uuid.uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def file_record(self, path):
        if not path:
            return None
        path = Path(path)
        if not path.resolve().is_relative_to(self.directory.resolve()) or path.is_symlink() or not path.is_file():
            return None
        stat = path.stat()
        if not stat.st_size:
            return None
        return {'path': path.relative_to(self.directory).as_posix(), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

    def valid_file(self, record):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            return None
        path = self.directory / record['path']
        try:
            return path if self.file_record(path) == record else None
        except (OSError, ValueError):
            return None

    def audio_directory(self, quality):
        path = self.directory / 'audio' / quality_value(quality)
        if quality_value(quality) not in {'fast', 'medium', 'slow'}:
            raise ValueError('未知音频质量')
        path.mkdir(parents=True, exist_ok=True)
        return path

    def video_directory(self):
        path = self.directory / 'video'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_video(self):
        return self.valid_file(self.read_json(self.directory / 'video.json').get('file'))

    def save_video(self, path):
        record = self.file_record(path)
        if record is None:
            raise ValueError('视频下载未完成，不能写入共享缓存')
        self.write_json(self.directory / 'video.json', {'file': record})

    def discard_invalid_media(self, quality=None):
        """Do not let a downloader's 'already exists' shortcut reuse a bad file."""
        if quality is None:
            record = self.read_json(self.directory / 'video.json').get('file')
            parent = self.video_directory()
        else:
            record = self.read_json(self.directory / f'audio-{quality_value(quality)}.json').get('audio')
            parent = self.audio_directory(quality)
        if not isinstance(record, dict) or self.valid_file(record) is not None:
            return
        value = record.get('path')
        if not isinstance(value, str):
            return
        path = self.directory / value
        if path.parent.resolve() == parent.resolve() and (path.is_file() or path.is_symlink()):
            path.unlink()

    def get_metadata(self, quality, need_audio=False):
        name = f'audio-{quality_value(quality)}.json' if need_audio else 'metadata.json'
        data = self.read_json(self.directory / name)
        if data.get('input_key') != self.input_key:
            return None
        audio = self.valid_file(data.get('audio'))
        if need_audio and audio is None:
            return None
        try:
            result = AudioDownloadResult(**data['metadata'])
        except (KeyError, TypeError):
            return None
        result.file_path = str(audio) if audio else ''
        video = self.get_video()
        result.video_path = str(video) if video else None
        return result

    def save_metadata(self, metadata, quality):
        record = self.file_record(metadata.file_path)
        data = {'input_key': self.input_key, 'metadata': asdict(metadata), 'audio': record}
        self.write_json(self.directory / 'metadata.json', data)
        if record:
            self.write_json(self.directory / f'audio-{quality_value(quality)}.json', data)

    def text_path(self, signature):
        return self.directory / 'text' / f'{signature}.json'

    @property
    def platform_text_path(self):
        return self.directory / 'text' / 'platform.json'

    def save_text(self, transcript, signature):
        raw = transcript.raw or {}
        if not transcript.segments or raw.get('input_key') != self.input_key:
            return
        if raw.get('source') == 'hard_subtitle_ocr' and raw.get('quality_status') != 'passed':
            return
        data = asdict(transcript)
        self.write_json(self.text_path(signature), data)
        if raw.get('source') in {'platform_subtitle', 'client_prefetched'}:
            self.write_json(self.platform_text_path, data)

    def legacy_entries(self, notes_root, identity):
        """Look up owned live tasks, never infer ownership from a file path."""
        from sqlalchemy import select
        from app.db import engine as db_module
        from app.db.models.video_tasks import VideoTask
        from app.db.models.task_jobs import TaskJob
        with db_module.SessionLocal() as db:
            rows = db.execute(select(VideoTask, TaskJob.payload).outerjoin(TaskJob, TaskJob.task_id == VideoTask.task_id).where(
                VideoTask.user_id == self.user_id, VideoTask.deleted.is_(False), VideoTask.platform == identity['platform'])).all()
        root = Path(notes_root).resolve()
        for task, payload in rows:
            try:
                form = json.loads(task.form_data or '{}')
                if source_identity(form.get('video_url', ''), task.platform) != identity:
                    continue
                directory = (root / task.storage_relpath).resolve()
                if not directory.is_relative_to(root):
                    continue
                snapshot = json.loads(payload or '{}').get('transcriber', {})
                yield task.task_id, directory, form, snapshot
            except (ValueError, TypeError):
                continue

    def import_file(self, path, task_id, target_directory):
        if not path:
            return None
        path = Path(path)
        # Legacy media may be purged immediately afterwards. Copy into independent
        # storage rather than keeping a symlink or an in-place writable hard link.
        root = (RUNTIME_ROOT / 'media' / 'users' / str(self.user_id) / task_id).resolve()
        target = target_directory / path.name
        temporary = target.with_name(target.name + f'.{uuid.uuid4().hex}.tmp')
        try:
            if not path.resolve().is_relative_to(root) or not path.is_file() or not path.stat().st_size:
                return None
            shutil.copyfile(path, temporary)
            temporary.replace(target)
            return target
        except FileNotFoundError:
            return None  # An old task was deleted while the cache was being adopted.
        finally:
            temporary.unlink(missing_ok=True)

    def import_media(self, entries, quality):
        for task_id, directory, form, _ in entries:
            data = self.read_json(directory / f'{task_id}_audio.json')
            old_key = cache_signature({'url': form.get('video_url', ''), 'platform': form.get('platform', '')})
            if data.pop('_input_key', None) not in {self.input_key, old_key}:
                continue
            try:
                metadata = AudioDownloadResult(**data)
            except TypeError:
                continue
            imported = False
            if self.get_video() is None:
                video = self.import_file(metadata.video_path, task_id, self.video_directory())
                if video:
                    self.save_video(video)
                    imported = True
            if quality_value(form.get('quality', '')) == quality_value(quality) and self.get_metadata(quality, need_audio=True) is None:
                audio = self.import_file(metadata.file_path, task_id, self.audio_directory(quality))
                if audio:
                    metadata.file_path = str(audio)
                    imported = True
                else:
                    metadata.file_path = ''
            else:
                metadata.file_path = ''
            if imported or self.get_metadata(quality) is None:
                self.save_metadata(metadata, quality)

    def import_text(self, entries, policy, signature, legacy_policy):
        """Adopt completed old results only when their original policy is verifiable."""
        from app.models.transcriber_model import TranscriptResult, TranscriptSegment
        for task_id, directory, form, snapshot in entries:
            data = self.read_json(directory / f'{task_id}_transcript.json')
            raw = data.get('raw') or {}
            if not isinstance(raw, dict):
                continue
            old_key = cache_signature({'url': form.get('video_url', ''), 'platform': form.get('platform', '')})
            if raw.get('input_key') not in {old_key, self.input_key}:
                continue
            platform_text = raw.get('source') in {'platform_subtitle', 'client_prefetched'}
            if not platform_text:
                old = {**legacy_policy, 'input_key': old_key,
                       'asr_model': snapshot.get('whisper_model_size', legacy_policy['asr_model']),
                       'asr_engine': snapshot.get('transcriber_type', legacy_policy['asr_engine'])}
                if policy['method'] == 'asr':
                    if raw.get('source') != 'asr' or any(old[k] != policy[k] for k in ('asr_model', 'asr_engine')):
                        continue
                    if quality_value(form.get('quality', '')) != policy['audio_quality']:
                        continue
                    if policy.get('asr_compute_type') and raw.get('compute_type') != policy['asr_compute_type']:
                        continue
                    if policy.get('asr_api_model'):
                        continue  # Old online results do not record their API model.
                else:
                    if raw.get('source') != 'hard_subtitle_ocr' or raw.get('quality_status') != 'passed':
                        continue
                    try:
                        same_fps = float(raw.get('fps', 0)) == policy['fps']
                    except (TypeError, ValueError):
                        continue
                    if raw.get('pipeline_version') != policy['version'] or not same_fps:
                        continue
                    device = (raw.get('runtime') or {}).get('device')
                    if device != policy['ocr_device']:
                        continue
                    old['ocr_device'] = device
                possible = [cache_signature({**old, 'audio_quality': q}) for q in
                            (quality_value(form.get('quality', '')), 'DownloadQuality.' + quality_value(form.get('quality', '')))]
                if raw.get('cache_signature') not in possible:
                    continue
            try:
                transcript = TranscriptResult(data.get('language'), data['full_text'],
                    [TranscriptSegment(**s) for s in data['segments']],
                    {**raw, 'input_key': self.input_key, 'cache_signature': signature})
                if not transcript.segments:
                    continue
            except (KeyError, TypeError):
                continue
            self.save_text(transcript, signature)
            logger.info('已复用历史字幕缓存 user_id=%s source=%s method=%s', self.user_id, self.input_key[:12], policy['method'])
            return transcript
