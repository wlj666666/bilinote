import re
from typing import List, Tuple
from app.utils.timestamps import TIMESTAMP_PATTERN, parse_timestamp


def extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
    pattern = rf"(\*?Screenshot-(?:\[({TIMESTAMP_PATTERN})\]|({TIMESTAMP_PATTERN})(?![\d:]))\*?)"
    results: List[Tuple[str, int]] = []
    for match in re.finditer(pattern, markdown):
        total_seconds = parse_timestamp(match.group(2) or match.group(3))
        results.append((match.group(1), total_seconds))
    return results
