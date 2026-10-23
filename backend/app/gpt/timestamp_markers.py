"""Resolve model references to source times without model-generated arithmetic."""
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
_MARKER_START = r'\b(?:Content|Screenshot)-(?=\[|t=|\d|\*|$)'
_MARKER = re.compile(r'\b(Content|Screenshot)-(?=\[|t=|\d)(?:\[([^\]\n]*)\]|([^\s*]+))')


def source_seconds(segments) -> set[int]:
    return {max(0, int(segment.start)) for segment in segments}


def resolve_timestamp_markers(markdown: str, allowed_seconds: set[int]) -> str:
    """Reject invented/legacy times; only the program formats elapsed hours."""
    def replace(match):
        token = match.group(2) or match.group(3) or ''
        anchor = re.fullmatch(r't=(\d+)', token) if match.group(2) is not None else None
        if anchor is None or int(anchor.group(1)) not in allowed_seconds:
            raise ValueError(f'模型返回了无效的原片时间编号：{match.group(0)}')
        return f'{match.group(1)}-[{format_timestamp(int(anchor.group(1)))}]'

    resolved = _MARKER.sub(replace, markdown)
    # Catch truncated/unclosed markers too, rather than caching unusable links.
    if len(re.findall(_MARKER_START, markdown)) != len(list(_MARKER.finditer(markdown))):
        raise ValueError('模型返回了不完整的原片时间标记')
    return resolved


def marker_seconds(markdown: str) -> set[int]:
    """Times available to the merge step, which has notes rather than subtitles."""
    return {int(value) for value in re.findall(r'\b(?:Content|Screenshot)-\[t=(\d+)\]', markdown)}
