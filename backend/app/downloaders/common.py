"""Bound native yt-dlp postprocessing without holding CPU slots during download."""
import yt_dlp
from app.services.task_resources import cpu_stage


class ResourceLimitedYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(self, params=None, *args, **kwargs):
        params = dict(params or {})
        params.setdefault('postprocessor_args', {
            'ffmpeg_i': ['-threads', '1'], 'ffmpeg_o': ['-threads', '1'],
        })
        super().__init__(params, *args, **kwargs)

    @cpu_stage
    def run_pp(self, pp, infodict):
        return super().run_pp(pp, infodict)
