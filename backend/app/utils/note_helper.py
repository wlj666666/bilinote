import re
from app.utils.timestamps import TIMESTAMP_PATTERN, parse_timestamp


def prepend_source_link(markdown: str | None, source_url: str) -> str | None:
    """
    在笔记开头添加来源链接；若首个非空行已包含来源链接，则更新该行并避免重复。
    """
    if markdown is None:
        return None

    source = (source_url or "").strip()
    if not source:
        return markdown

    header = f"> 来源链接：{source}"
    lines = markdown.splitlines()
    first_non_empty_idx = None
    for idx, line in enumerate(lines):
        if line.strip():
            first_non_empty_idx = idx
            break

    if first_non_empty_idx is not None:
        first_line = lines[first_non_empty_idx].strip()
        if first_line.startswith("> 来源链接：") or first_line.startswith("来源链接："):
            lines[first_non_empty_idx] = header
            return "\n".join(lines)

    if markdown.strip():
        return f"{header}\n\n{markdown}"
    return header


def replace_content_markers(markdown: str, video_id: str, platform: str = 'bilibili') -> str:
    """
    将 Content 标记转换为原片链接，支持 mm:ss、累计分钟及 hh:mm:ss。
    """
    pattern = rf"\*?Content-(?:\[({TIMESTAMP_PATTERN})\]|({TIMESTAMP_PATTERN})(?![\d:]))\*?"

    safe_video_id = video_id

    def replacer(match):
        timestamp = match.group(1) or match.group(2)
        total_seconds = parse_timestamp(timestamp)

        if platform == 'bilibili':
            parsed_video_id = safe_video_id.replace("_p", "?p=")
            separator = '&' if '?' in parsed_video_id else '?'
            url = f"https://www.bilibili.com/video/{parsed_video_id}{separator}t={total_seconds}"
        elif platform == 'youtube':
            url = f"https://www.youtube.com/watch?v={safe_video_id}&t={total_seconds}s"
        elif platform == 'douyin':
            url = f"https://www.douyin.com/video/{safe_video_id}"
            return f"[原片 @ {timestamp}]({url})"
        else:
            return f"({timestamp})"

        return f"[原片 @ {timestamp}]({url})"

    # Models sometimes put a timestamp inside a TOC link label. Drop that wrapper
    # before expanding the timestamp, otherwise the output contains nested links.
    markdown = re.sub(
        r"\[([^\n]*?)\]\(#[^\n)]*\)",
        lambda match: match.group(1) if re.search(pattern, match.group(1)) else match.group(0),
        markdown,
    )
    return re.sub(pattern, replacer, markdown)
