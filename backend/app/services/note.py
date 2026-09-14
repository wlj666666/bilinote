import json
import hashlib
import inspect
import subprocess
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple, Union, Any

from fastapi import HTTPException
from pydantic import HttpUrl
from dotenv import load_dotenv

from app.downloaders.base import Downloader
from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.downloaders.douyin_downloader import DouyinDownloader
from app.downloaders.local_downloader import LocalDownloader
from app.downloaders.youtube_downloader import YoutubeDownloader
from app.db.video_task_dao import delete_task_by_video, insert_video_task
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.enmus.task_status_enums import TaskStatus
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.gpt.base import GPT
from app.gpt.gpt_factory import GPTFactory
from app.models.audio_model import AudioDownloadResult
from app.models.gpt_model import GPTSource
from app.models.model_config import ModelConfig
from app.models.notes_model import AudioDownloadResult, NoteResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.constant import SUPPORT_PLATFORM_MAP
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.transcriber.transcriber_provider import get_transcriber, _transcribers
from app.utils.note_helper import replace_content_markers, prepend_source_link
from app.utils.screenshot_marker import extract_screenshot_timestamps
from app.utils.status_code import StatusCode
from app.utils.video_helper import generate_screenshot
from app.utils.video_reader import VideoReader
from app.services.hard_subtitle_extractor import HardSubtitleExtractor, PIPELINE_VERSION, atomic_json
from app.utils.path_helper import get_app_dir

# ------------------ 环境变量与全局配置 ------------------

# 从 .env 文件中加载环境变量
load_dotenv()

# 后端 API 地址与端口（若有需要可以在代码其他部分使用 BACKEND_BASE_URL）
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
BACKEND_PORT = os.getenv("BACKEND_PORT", "8483")
BACKEND_BASE_URL = f"{API_BASE_URL}:{BACKEND_PORT}"

# 输出目录（用于缓存音频、转写、Markdown 文件，以及存储截图）
NOTE_OUTPUT_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_OUTPUT_DIR = os.getenv("OUT_DIR", "./static/screenshots")
# 图片基础 URL（用于生成 Markdown 中的图片链接，需前端静态目录对应）
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "/static/screenshots")

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NoteGenerator:
    """
    NoteGenerator 用于执行视频/音频下载、转写、GPT 生成笔记、插入截图/链接、
    以及将任务信息写入状态文件与数据库等功能。
    """

    def __init__(self):
        from app.services.transcriber_config_manager import TranscriberConfigManager
        config_manager = TranscriberConfigManager()
        self.model_size: str = config_manager.get_whisper_model_size()
        self.device: Optional[str] = None
        self.transcriber_type: str = config_manager.get_transcriber_type()
        self.transcriber: Optional[Transcriber] = None
        self.video_path: Optional[Path] = None
        self.video_img_urls=[]
        logger.info("NoteGenerator 初始化完成")


    # ---------------- 公有方法 ----------------

    def generate(
        self,
        video_url: Union[str, HttpUrl],
        platform: str,
        quality: DownloadQuality = DownloadQuality.medium,
        task_id: Optional[str] = None,
        model_name: Optional[str] = None,
        provider_id: Optional[str] = None,
        link: bool = False,
        screenshot: bool = False,
        _format: Optional[List[str]] = None,
        style: Optional[str] = None,
        extras: Optional[str] = None,
        output_path: Optional[str] = None,
        video_understanding: bool = False,
        video_interval: int = 0,
        grid_size: Optional[List[int]] = None,
        text_extraction_method: str = "asr",
    ) -> NoteResult | None:
        """
        主流程：按步骤依次下载、转写、GPT 总结、截图/链接处理、存库、返回 NoteResult。

        :param video_url: 视频或音频链接
        :param platform: 平台名称，对应 SUPPORT_PLATFORM_MAP 中的键
        :param quality: 下载音频的质量枚举
        :param task_id: 用于标识本次任务的唯一 ID，亦用于状态文件和缓存文件命名
        :param model_name: GPT 模型名称
        :param provider_id: 模型供应商 ID
        :param link: 是否在笔记中插入视频片段链接
        :param screenshot: 是否在笔记中替换 Screenshot 标记为图片
        :param _format: 包含 'link' 或 'screenshot' 等字符串的列表，决定后续处理
        :param style: GPT 生成笔记的风格
        :param extras: 额外参数，传递给 GPT
        :param output_path: 下载输出目录（可选）
        :param video_understanding: 是否需要视频拼图理解（生成缩略图）
        :param video_interval: 视频帧截取间隔（秒），仅在 video_understanding 为 True 时生效
        :param grid_size: 生成缩略图时的网格大小，如 [3, 3]
        :return: NoteResult 对象，包含 markdown 文本、转写结果和音频元信息
        """
        if grid_size is None:
            grid_size = []

        try:
            logger.info(f"开始生成笔记 (task_id={task_id})")
            self._update_status(task_id, TaskStatus.PARSING)

            # 获取下载器与 GPT 实例

            downloader = self._get_downloader(platform)
            gpt = self._get_gpt(model_name, provider_id)

            # 缓存文件路径
            audio_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_audio.json"
            transcript_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_transcript.json"
            markdown_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_markdown.md"
            if text_extraction_method not in {"asr", "ocr"}:
                raise ValueError("文字提取方式必须是 asr 或 ocr")
            # Bind retries to the input and extraction policy, never just a task ID.
            identity = {"url": str(video_url), "platform": platform}
            if platform == "local":
                local_path = Path(downloader.download_video(video_url))
                identity.update(size=local_path.stat().st_size, mtime=local_path.stat().st_mtime_ns)
            input_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            policy = {"input_key": input_key, "method": text_extraction_method,
                      "version": PIPELINE_VERSION, "fps": os.getenv("OCR_FPS", "5"),
                      "asr_model": self.model_size, "asr_engine": self.transcriber_type,
                      "audio_quality": str(quality), "ocr_model": "rapidocr-3.9.2-ppocrv6"}
            if text_extraction_method == "ocr":
                policy["ocr_device"] = os.getenv("OCR_DEVICE", "cpu").strip().lower()
            signature = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
            transcript = self._load_text_cache(transcript_cache_file, signature, input_key)
            if transcript is None:
                try:
                    transcript = downloader.download_subtitles(video_url)
                    if not transcript or not transcript.segments:
                        transcript = None
                    else:
                        transcript.raw = {**(transcript.raw or {}), "source": "platform_subtitle"}
                except Exception as exc:
                    logger.warning(f"平台字幕获取失败，将使用所选提取方式: {exc}")

            needs_ocr = transcript is None and text_extraction_method == "ocr"
            audio_meta = self._download_media(
                downloader=downloader, video_url=video_url, quality=quality,
                audio_cache_file=audio_cache_file, status_phase=TaskStatus.DOWNLOADING,
                platform=platform, output_path=output_path, screenshot=screenshot,
                video_understanding=video_understanding, video_interval=video_interval,
                grid_size=grid_size, skip_download=transcript is not None or needs_ocr,
                require_video=needs_ocr, input_key=input_key,
            )
            if transcript is None:
                if needs_ocr:
                    last_progress = [None]
                    def report_progress(phase, fraction):
                        value = (phase, int(fraction*100))
                        if value != last_progress[0]:
                            last_progress[0] = value
                            state = TaskStatus.DETECTING_SUBTITLES if phase == "detect" else TaskStatus.EXTRACTING_SUBTITLES
                            label = "定位字幕" if phase == "detect" else "提取字幕"
                            self._update_status(task_id, state, f"{label}，已处理 {value[1]}%")
                    result = HardSubtitleExtractor().extract(
                        self.video_path, NOTE_OUTPUT_DIR / f"{task_id}_ocr_diagnostics.json", report_progress)
                    if result.status != "success":
                        raise RuntimeError(result.message)
                    transcript = result.transcript
                else:
                    transcript = self._transcribe_audio(audio_meta.file_path, transcript_cache_file,
                                                        TaskStatus.TRANSCRIBING)
                    raw = asdict(transcript).get("raw") or {}
                    transcript.raw = {**(raw if isinstance(raw, dict) else {"original": raw}), "source": "asr"}
            transcript.raw = {**(transcript.raw or {}), "input_key": input_key, "cache_signature": signature}
            atomic_json(transcript_cache_file, asdict(transcript))

            # 3. GPT 总结
            markdown = self._summarize_text(
                audio_meta=audio_meta,
                transcript=transcript,
                gpt=gpt,
                markdown_cache_file=markdown_cache_file,
                link=link,
                screenshot=screenshot,
                formats=_format or [],
                style=style,
                extras=extras,
                video_img_urls=self.video_img_urls,
            )

            # 4. 截图 & 链接替换
            if _format:
                markdown = self._post_process_markdown(
                    markdown=markdown,
                    video_path=self.video_path,
                    formats=_format,
                    audio_meta=audio_meta,
                    platform=platform,
                )

            markdown = prepend_source_link(markdown, str(video_url))

            # 5. 保存记录到数据库
            self._update_status(task_id, TaskStatus.SAVING)
            self._save_metadata(video_id=audio_meta.video_id, platform=platform, task_id=task_id)

            # 6. 完成
            self._update_status(task_id, TaskStatus.SUCCESS)
            logger.info(f"笔记生成成功 (task_id={task_id})")
            return NoteResult(markdown=markdown, transcript=transcript, audio_meta=audio_meta)

        except Exception as exc:
            logger.error(f"生成笔记流程异常 (task_id={task_id})：{exc}", exc_info=True)
            self._update_status(task_id, TaskStatus.FAILED, message=str(exc))
            return None

    @staticmethod
    def delete_note(video_id: str, platform: str) -> int:
        """
        删除数据库中对应 video_id 与 platform 的任务记录

        :param video_id: 视频 ID
        :param platform: 平台标识
        :return: 删除的记录数
        """
        logger.info(f"删除笔记记录 (video_id={video_id}, platform={platform})")
        return delete_task_by_video(video_id, platform)

    # ---------------- 私有方法 ----------------

    def _init_transcriber(self) -> Transcriber:
        """
        根据环境变量 TRANSCRIBER_TYPE 动态获取并实例化转写器
        """
        if self.transcriber_type not in _transcribers:
            logger.error(f"未找到支持的转写器：{self.transcriber_type}")
            raise Exception(f"不支持的转写器：{self.transcriber_type}")

        logger.info(f"使用转写器：{self.transcriber_type}")
        return get_transcriber(
            transcriber_type=self.transcriber_type,
            model_size=self.model_size,
        )

    def _get_gpt(self, model_name: Optional[str], provider_id: Optional[str]) -> GPT:
        """
        根据 provider_id 获取对应的 GPT 实例
        :param model_name: GPT 模型名称
        :param provider_id: 供应商 ID
        :return: GPT 实例
        """
        provider = ProviderService.get_provider_by_id(provider_id)
        if not provider:
            logger.error(f"[get_gpt] 未找到模型供应商: provider_id={provider_id}")
            raise ProviderError(code=ProviderErrorEnum.NOT_FOUND,message=ProviderErrorEnum.NOT_FOUND.message)
        logger.info(f"创建 GPT 实例 {provider_id}")
        config = ModelConfig(
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model_name=model_name,
            provider=provider["type"],
            name=provider["name"],
        )
        return GPTFactory().from_config(config)

    def _get_downloader(self, platform: str) -> Downloader:
        """
        根据平台名称获取对应的下载器实例

        :param platform: 平台标识，需在 SUPPORT_PLATFORM_MAP 中
        :return: 对应的 Downloader 子类实例
        """
        downloader_cls = SUPPORT_PLATFORM_MAP.get(platform)
        logger.debug(f"实例化下载器 -  {platform}")
        instance = None
        if not downloader_cls:
            logger.error(f"不支持的平台：{platform}")
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        try:
            instance = downloader_cls
        except Exception as e:
            logger.error(f"实例化下载器失败：{e}")


        logger.info(f"使用下载器：{downloader_cls.__class__}")
        return instance

    def _update_status(self, task_id: Optional[str], status: Union[str, TaskStatus], message: Optional[str] = None):
        """
        创建或更新 {task_id}.status.json，记录当前任务状态

        :param task_id: 任务唯一 ID
        :param status: TaskStatus 枚举或自定义状态字符串
        :param message: 可选消息，用于记录失败原因等
        """
        if not task_id:
            return

        NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        status_file = NOTE_OUTPUT_DIR / f"{task_id}.status.json"
        print(f"写入状态文件: {status_file} 当前状态: {status}")
        data = {"status": status.value if isinstance(status, TaskStatus) else status}
        if message:
            data["message"] = message

        try:
            # First create a temporary file
            temp_file = status_file.with_suffix('.tmp')

            # Write to temporary file
            with temp_file.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Atomic rename operation
            temp_file.replace(status_file)

            print(f"状态文件写入成功: {status_file}")
        except Exception as e:
            logger.error(f"写入状态文件失败 (task_id={task_id})：{e}")
            # Try to write error to file directly as fallback
            try:
                with status_file.open('w', encoding='utf-8') as f:
                    f.write(f"Error writing status: {str(e)}")
            except:
                logger.error(f"写入错误  {e}")

    def _handle_exception(self, task_id, exc):
        logger.error(f"任务异常 (task_id={task_id})", exc_info=True)
        error_message = getattr(exc, 'detail', str(exc))
        if isinstance(error_message, dict):
            try:
                error_message = json.dumps(error_message, ensure_ascii=False)
            except:
                error_message = str(error_message)
        self._update_status(task_id, TaskStatus.FAILED, message=error_message)

    @staticmethod
    def _load_text_cache(path, signature, input_key):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("raw") or {}
            if raw.get("input_key") != input_key:
                return None
            shared_platform = raw.get("source") in {"platform_subtitle", "client_prefetched"} and raw.get("input_key") == input_key
            if raw.get("cache_signature") != signature and not shared_platform:
                return None
            segments = [TranscriptSegment(**seg) for seg in data["segments"]]
            if not segments:
                return None
            return TranscriptResult(data.get("language"), data["full_text"], segments, raw)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _download_media(self, downloader, video_url, quality, audio_cache_file,
                        status_phase, platform, output_path, screenshot,
                        video_understanding, video_interval, grid_size,
                        skip_download=False, require_video=False, input_key=""):
        task_id = audio_cache_file.stem.split("_")[0]
        self._update_status(task_id, status_phase)
        # URL (including Bilibili p) scoped directories avoid cross-part cache collisions.
        media_dir = str(Path(output_path or get_app_dir("media")) / input_key[:24])
        Path(media_dir).mkdir(parents=True, exist_ok=True)
        need_video = require_video or screenshot or video_understanding
        self.video_img_urls = []
        self.video_path = None
        if need_video:
            self.video_path = Path(downloader.download_video(video_url, output_dir=media_dir))
            if not self.video_path.is_file():
                raise ValueError("无法获得可解码视频文件")
            if screenshot or video_understanding:
                self.video_img_urls = VideoReader(
                    video_path=str(self.video_path), grid_size=tuple(grid_size or [2, 2]),
                    frame_interval=video_interval if video_interval and video_interval > 0 else 6,
                    unit_width=960, unit_height=540, save_quality=80,
                    frame_dir=str(Path(media_dir)/task_id/"frames"),
                    grid_dir=str(Path(media_dir)/task_id/"grids"),
                ).run()
        cached = None
        try:
            record = json.loads(audio_cache_file.read_text(encoding="utf-8"))
            if record.pop("_input_key", None) == input_key:
                cached = AudioDownloadResult(**record)
                if not skip_download and (not cached.file_path or not Path(cached.file_path).is_file()):
                    cached = None
        except (OSError, ValueError, TypeError):
            pass
        if cached is None:
            kwargs = dict(video_url=video_url, quality=quality, output_dir=media_dir, need_video=need_video)
            extracted_audio = None
            if not skip_download and self.video_path:
                import av
                with av.open(str(self.video_path)) as container:
                    has_audio = bool(container.streams.audio)
                if has_audio:
                    extracted_audio = str(Path(media_dir) / "audio.mp3")
                    subprocess.run(["ffmpeg", "-y", "-i", str(self.video_path), "-vn",
                                    "-codec:a", "libmp3lame", "-q:a", "4", extracted_audio],
                                   check=True, capture_output=True)
            if "skip_download" in inspect.signature(downloader.download).parameters:
                kwargs["skip_download"] = skip_download or extracted_audio is not None
                cached = downloader.download(**kwargs)
                if extracted_audio:
                    cached.file_path = extracted_audio
            elif not skip_download:
                cached = downloader.download(**kwargs)
            else:
                # Older platform adapters cannot fetch metadata without audio. Probe the
                # video locally instead; OCR must never silently trigger an audio download.
                if self.video_path is None:
                    self.video_path = Path(downloader.download_video(video_url, output_dir=media_dir))
                cached = AudioDownloadResult("", self.video_path.stem,
                    HardSubtitleExtractor.duration(self.video_path), None, platform,
                    self.video_path.stem, {}, str(self.video_path))
        if self.video_path:
            cached.video_path = str(self.video_path)
        atomic_json(audio_cache_file, {**asdict(cached), "_input_key": input_key})
        return cached

    def _transcribe_audio(
        self,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
    ) -> TranscriptResult | None:
        """
        1. 检查转写缓存；若存在则尝试加载，否则调用转写器生成并缓存。
        2. 返回 TranscriptResult 对象

        :param audio_file: 音频文件本地路径
        :param transcript_cache_file: 转写结果缓存路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.TRANSCRIBING
        :return: TranscriptResult 对象
        """
        task_id = transcript_cache_file.stem.split("_")[0]
        self._update_status(task_id, status_phase)

        # 调用转写器
        try:
            logger.info("开始转写音频")
            if self.transcriber is None:
                from app.services.transcriber_config_manager import TranscriberConfigManager
                readiness = TranscriberConfigManager().is_model_ready()
                if not readiness["ready"]:
                    raise RuntimeError(readiness["reason"])
                self.transcriber = self._init_transcriber()
            transcript = self.transcriber.transcript(file_path=audio_file)
            transcript_cache_file.write_text(json.dumps(asdict(transcript), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"转写并缓存成功 ({transcript_cache_file})")
            return transcript
        except Exception as exc:
            logger.error(f"音频转写失败：{exc}")
            self._handle_exception(task_id, exc)
            raise

    def _summarize_text(
        self,
        audio_meta: AudioDownloadResult,
        transcript: TranscriptResult,
        gpt: GPT,
        markdown_cache_file: Path,
        link: bool,
        screenshot: bool,
        formats: List[str],
        style: Optional[str],
        extras: Optional[str],
            video_img_urls: List[str],
    ) -> str | None:
        """
        调用 GPT 对转写结果进行总结，生成 Markdown 文本并缓存。

        :param audio_meta: AudioDownloadResult 元信息
        :param transcript: TranscriptResult 转写结果
        :param gpt: GPT 实例
        :param markdown_cache_file: Markdown 缓存路径
        :param link: 是否在笔记中插入链接
        :param screenshot: 是否在笔记中生成截图占位
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param style: GPT 输出风格
        :param extras: GPT 额外参数
        :return: 生成的 Markdown 字符串
        """
        task_id = markdown_cache_file.stem
        self._update_status(task_id, TaskStatus.SUMMARIZING)

        source = GPTSource(
            title=audio_meta.title,
            segment=transcript.segments,
            tags=audio_meta.raw_info.get("tags", []),
            screenshot=screenshot,
            video_img_urls=video_img_urls,
            link=link,
            _format=formats,
            style=style,
            extras=extras,
            checkpoint_key=task_id,
        )

        try:
            markdown = gpt.summarize(source)
            markdown_cache_file.write_text(markdown, encoding="utf-8")
            logger.info(f"GPT 总结并缓存成功 ({markdown_cache_file})")
            return markdown
        except Exception as exc:
            logger.error(f"GPT 总结失败：{exc}")
            self._handle_exception(task_id, exc)
            raise

    def _post_process_markdown(
        self,
        markdown: str,
        video_path: Optional[Path],
        formats: List[str],
        audio_meta: AudioDownloadResult,
        platform: str,
    ) -> str:
        """
        对生成的 Markdown 做后期处理：插入截图和/或插入链接。

        :param markdown: 原始 Markdown 字符串
        :param video_path: 本地视频路径（可为 None）
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param audio_meta: AudioDownloadResult 元信息，用于链接替换
        :param platform: 平台标识，用于链接替换
        :return: 处理后的 Markdown 字符串
        """
        if "screenshot" in formats and video_path:
            try:
                markdown = self._insert_screenshots(markdown, video_path)
            except Exception as exc:
                logger.warning("截图插入失败，跳过该步骤")

        if "link" in formats:
            try:
                markdown = replace_content_markers(markdown, video_id=audio_meta.video_id, platform=platform)
            except Exception as e:
                logger.warning(f"链接插入失败，跳过该步骤：{e}")

        return markdown

    def _insert_screenshots(self, markdown: str, video_path: Path) -> str | None | Any:
        """
        扫描 Markdown 文本中所有 Screenshot 标记，并替换为实际生成的截图链接。

        :param markdown: 含有 *Screenshot-mm:ss 或 Screenshot-[mm:ss] 标记的 Markdown 文本
        :param video_path: 本地视频文件路径
        :return: 替换后的 Markdown 字符串
        """
        matches: List[Tuple[str, int]] = extract_screenshot_timestamps(markdown)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(str(video_path), str(IMAGE_OUTPUT_DIR), ts, idx)
                filename = Path(img_path).name
                # 构建前端可访问的 URL，例如 /static/screenshots/{filename}
                img_url = f"{IMAGE_BASE_URL.rstrip('/')}/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
            except Exception as exc:
                logger.error(f"生成截图失败 (timestamp={ts})：{exc}")
                # self._handle_exception(task_id, exc)
                return None
        return markdown

    @staticmethod
    def _extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
        """
        从 Markdown 文本中提取所有 '*Screenshot-mm:ss' 或 'Screenshot-[mm:ss]' 标记，
        返回 [(原始标记文本, 时间戳秒数), ...] 列表。

        :param markdown: 原始 Markdown 文本
        :return: 标记与对应时间戳秒数的列表
        """
        return extract_screenshot_timestamps(markdown)

    def _save_metadata(self, video_id: str, platform: str, task_id: str) -> None:
        """
        将生成的笔记任务记录插入数据库

        :param video_id: 视频 ID
        :param platform: 平台标识
        :param task_id: 任务 ID
        """
        try:
            insert_video_task(video_id=video_id, platform=platform, task_id=task_id)
            logger.info(f"已保存任务记录到数据库 (video_id={video_id}, platform={platform}, task_id={task_id})")
        except Exception as e:
            logger.error(f"保存任务记录失败：{e}")
