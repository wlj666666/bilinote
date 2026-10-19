"""Elapsed video times shared by prompts, source links and screenshots."""
import re


# Accept elapsed minutes (including 100+) and explicit hours without wrapping.
TIMESTAMP_PATTERN = r'(?:\d+:[0-5]\d:[0-5]\d|\d{2,}:[0-5]\d)'


def format_timestamp(seconds: float) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    return f'{minutes:02d}:{seconds:02d}'


def parse_timestamp(timestamp: str) -> int:
    if not re.fullmatch(TIMESTAMP_PATTERN, timestamp):
        raise ValueError(f'无效视频时间戳: {timestamp}')
    parts = [int(part) for part in timestamp.split(':')]
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    minutes, seconds = parts
    return minutes * 60 + seconds
