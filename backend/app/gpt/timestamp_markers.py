"""Validate source references and correct small errors inside subtitle intervals."""
import re

from app.utils.timestamps import format_timestamp


SOURCE_TIME_PROMPT = '''
时间标记规则：每条字幕的 [t=整数] 是原视频绝对秒数，也是唯一可用的时间编号。
原片跳转必须写成 *Content-[t=整数]*，截图必须写成 *Screenshot-[t=整数]*。
只能逐字复制本次材料中与内容对应的时间编号，不得自行计算、缩写或编造时间。
例如字幕为「01:10:40 [t=4240] - 内容」，应输出 *Content-[t=4240]*。
不要输出 mm:ss 或 hh:mm:ss 时间标记；程序会把编号转换为包含小时的显示时间。
'''

# Restrict this to time markers; ordinary text such as HTTP Content-Type is valid.
_MARKER = re.compile(r'\b(Content|Screenshot)-(?=\[|t=|\d)(?:\[([^\]\n]*)\]|([^\s*]+))')
# Include emphasis wrappers and incomplete markers so fallback removes the whole
# placeholder, without deleting the heading or the following line of prose.
_REFERENCE = re.compile(
    r'(?P<open>\*{0,2})(?P<marker>\b(?:Content|Screenshot)-(?=\[|t=|\d|\*|$)'
    r'(?:\[[^\]\n*]*\]|\[[^\]\n*\s]*|[^\s*]+)?)(?P<close>\*{0,2})',
    re.MULTILINE,
)
MAX_INTERVAL_CORRECTION_SECONDS = 2
TIMESTAMP_WARNING = '> 提示：部分原片跳转或截图未能生成，笔记正文已保留。'


def source_seconds(segments) -> set[int]:
    return {max(0, int(segment.start)) for segment in segments}


def timestamp_marker_count(markdown: str) -> int:
    return len(list(_REFERENCE.finditer(markdown)))


def normalize_timestamp_markers(
    markdown: str, allowed_seconds: set[int], segments=(), *, drop_invalid: bool = False,
) -> str:
    """Keep source IDs internally, snapping only small, unambiguous interval errors.

    Merge callers omit segments: they may only reuse IDs in their own inputs.
    Never snap across subtitle gaps, hour boundaries by arithmetic, or chunks.
    """
    def replace(reference):
        marker = reference.group('marker')
        match = _MARKER.fullmatch(marker)
        token = match.group(2) if match else None
        anchor = re.fullmatch(r't=(\d+)', token or '')
        seconds = int(anchor.group(1)) if anchor and len(anchor.group(1)) <= 20 else None
        if seconds is not None and seconds not in allowed_seconds:
            candidates = {
                max(0, int(segment.start)) for segment in segments
                if segment.start <= seconds < segment.end
                and 0 < seconds - max(0, int(segment.start)) <= MAX_INTERVAL_CORRECTION_SECONDS
                and max(0, int(segment.start)) in allowed_seconds
            }
            seconds = candidates.pop() if len(candidates) == 1 else None
        if seconds is None:
            if drop_invalid:
                return ''
            raise ValueError(f'模型返回了无效的原片时间编号：{marker}')
        return f"{reference.group('open')}{match.group(1)}-[t={seconds}]{reference.group('close')}"

    return _REFERENCE.sub(replace, markdown)


def resolve_timestamp_markers(markdown: str, allowed_seconds: set[int]) -> str:
    """Reject invented/legacy times; only the program formats elapsed hours."""
    def replace(match):
        token = match.group(2) or match.group(3) or ''
        anchor = re.fullmatch(r't=(\d+)', token) if match.group(2) is not None else None
        if anchor is None or int(anchor.group(1)) not in allowed_seconds:
            raise ValueError(f'模型返回了无效的原片时间编号：{match.group(0)}')
        return f'{match.group(1)}-[{format_timestamp(int(anchor.group(1)))}]'

    return _MARKER.sub(replace, normalize_timestamp_markers(markdown, allowed_seconds))


def marker_seconds(markdown: str) -> set[int]:
    """Times available to the merge step, which has notes rather than subtitles."""
    return {int(value) for value in re.findall(r'\b(?:Content|Screenshot)-\[t=(\d+)\]', markdown)}
