from pathlib import Path
from shutil import copyfile

import av
import numpy as np
import pytest

from app.downloaders.bilibili_downloader import BilibiliDownloader


VIDEO_ID = "BV1fmEi6DE9b"
VIDEO_URL = f"https://www.bilibili.com/video/{VIDEO_ID}/"


def make_video(path: Path):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        for _ in range(10):
            frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_corrupt_cached_video_is_redownloaded(tmp_path, monkeypatch):
    video = tmp_path / f"{VIDEO_ID}.mp4"
    video.write_bytes(b"invalid video")
    valid = tmp_path / "valid.mp4"
    make_video(valid)
    opts_seen = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            opts_seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, url, download):
            assert url == VIDEO_URL and download
            assert not video.exists()
            copyfile(valid, video)
            return {"id": VIDEO_ID}

    monkeypatch.setattr("app.downloaders.bilibili_downloader.yt_dlp.YoutubeDL", FakeYoutubeDL)
    downloader = object.__new__(BilibiliDownloader)
    downloader._cookiefile = None

    assert downloader.download_video(VIDEO_URL, str(tmp_path)) == str(video)
    assert opts_seen[0]["format"].startswith("bv*[vcodec^=avc1][ext=mp4]/")
    assert downloader.download_video(VIDEO_URL, str(tmp_path)) == str(video)
    assert len(opts_seen) == 1


def test_first_download_recovers_from_corrupt_transfer(tmp_path, monkeypatch):
    valid = tmp_path / "valid.mp4"
    make_video(valid)
    video = tmp_path / f"{VIDEO_ID}.mp4"
    attempts = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, url, download):
            assert not video.exists()
            attempts.append(1)
            if len(attempts) == 1:
                video.write_bytes(b"invalid video")
            else:
                copyfile(valid, video)
            return {"id": VIDEO_ID}

    monkeypatch.setattr("app.downloaders.bilibili_downloader.yt_dlp.YoutubeDL", FakeYoutubeDL)
    downloader = object.__new__(BilibiliDownloader)
    downloader._cookiefile = None

    assert downloader.download_video(VIDEO_URL, str(tmp_path)) == str(video)
    assert len(attempts) == 2


def test_new_corrupt_download_is_rejected(tmp_path, monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, url, download):
            (tmp_path / f"{VIDEO_ID}.mp4").write_bytes(b"invalid video")
            return {"id": VIDEO_ID}

    monkeypatch.setattr("app.downloaders.bilibili_downloader.yt_dlp.YoutubeDL", FakeYoutubeDL)
    downloader = object.__new__(BilibiliDownloader)
    downloader._cookiefile = None

    with pytest.raises(ValueError, match="无法完整解码"):
        downloader.download_video(VIDEO_URL, str(tmp_path))


def test_ffmpeg_error_output_rejects_video_even_with_zero_exit_code(monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        "app.downloaders.bilibili_downloader.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, "", "Error parsing OBU data"),
    )
    assert not BilibiliDownloader._video_decodes_completely("cached.mp4")


def test_harmless_av1_metadata_warning_is_accepted(monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        "app.downloaders.bilibili_downloader.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, "", "[libdav1d] Unknown Metadata OBU type 6\n    Last message repeated 443 times\n"
        ),
    )
    assert BilibiliDownloader._video_decodes_completely("cached.mp4")
