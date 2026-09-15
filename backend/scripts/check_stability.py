"""Run isolated regression/fault tests and optionally decode cached videos.

From backend: python scripts/check_stability.py --media
No external model calls, downloads, production restarts or user-task writes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = [
    'test_stability', 'test_task_reliability', 'test_text_extraction_routes',
    'test_hard_subtitles', 'test_task_serial_executor', 'test_request_chunker',
    'test_video_reader_dedupe', 'test_note_helper', 'test_universal_gpt_checkpoint',
    'test_universal_gpt_content_format', 'test_ydl_retry_opts',
    'test_ydl_retry_behavior', 'test_url_normalize', 'test_youtube_metadata_only',
]
MEDIA_CODE = '''
import json, pathlib, sys, tempfile, time
import ffmpeg
from app.utils.video_reader import VideoReader
video = pathlib.Path(sys.argv[1])
duration = float(ffmpeg.probe(str(video))['format']['duration'])
started = time.monotonic()
with tempfile.TemporaryDirectory(prefix='bilinote-stability-media-') as tmp:
    root = pathlib.Path(tmp)
    reader = VideoReader(str(video), grid_size=(2,2), frame_interval=2,
        unit_width=480, unit_height=270, save_quality=75,
        frame_dir=str(root/'frames'), grid_dir=str(root/'grids'))
    images = reader.run()
    times = [reader.extract_time_from_filename(p.name) for p in (root/'frames').glob('*.jpg')]
    assert images and len(images) <= 24
    assert max(times) >= int(duration)-2
    print('MEDIA_RESULT_JSON '+json.dumps({'video':video.name, 'duration_seconds':duration,
        'elapsed_seconds':time.monotonic()-started, 'grids':len(images),
        'base64_bytes':sum(len(s) for s in images), 'last_frame_seconds':max(times)}))
'''


def run_isolated(command):
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        # Kill only this test's process group, including any test HTTP servers.
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--media', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT/'data/stability/latest.json')
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error('--repeat must be between 1 and 100')
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'suites': [], 'media': [],
              'scope': 'isolated local tests; no real model requests or production task writes'}
    failed = False
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='bilinote-stability-tests-') as tmp:
        for repeat in range(args.repeat):
            for name in SUITES:
                xml = Path(tmp)/f'{repeat}-{name}.xml'
                try:
                    process = run_isolated([sys.executable, '-m', 'pytest', f'tests/{name}.py',
                        '-q', '--tb=short', '-o', 'junit_family=legacy', f'--junitxml={xml}'])
                    item = {'name': name, 'iteration': repeat+1, 'ok': process.returncode == 0}
                    if xml.exists():
                        tree = ET.parse(xml)
                        item['tests'] = [{**test.attrib, 'properties': {
                            prop.get('name'): prop.get('value') for prop in test.findall('./properties/property')},
                            'failed': test.find('failure') is not None or test.find('error') is not None,
                            'skipped': test.find('skipped') is not None}
                            for test in tree.findall('.//testcase')]
                    if not item['ok']:
                        item['output'] = (process.stdout+process.stderr)[-12000:]
                except subprocess.TimeoutExpired:
                    item = {'name': name, 'iteration': repeat+1, 'ok': False, 'error': '90s test timeout'}
                failed |= not item['ok']
                report['suites'].append(item)
                print(name, 'PASS' if item['ok'] else 'FAIL', flush=True)
        if args.media:
            for video in sorted((ROOT/'data/media').glob('**/*.mp4'))[:3]:
                try:
                    process = run_isolated([sys.executable, '-c', MEDIA_CODE, str(video)])
                    line = next((line for line in process.stdout.splitlines() if line.startswith('MEDIA_RESULT_JSON ')), None)
                    item = json.loads(line.split(' ',1)[1]) if line else {'video': video.name}
                    item['ok'] = process.returncode == 0 and line is not None
                    if not item['ok']: item['output'] = (process.stdout+process.stderr)[-4000:]
                except subprocess.TimeoutExpired:
                    item = {'video': video.name, 'ok': False, 'error': '90s media timeout'}
                failed |= not item['ok']
                report['media'].append(item)
                print(video.name, 'PASS' if item['ok'] else 'FAIL', flush=True)
    report['elapsed_seconds'] = time.monotonic()-started
    report['ok'] = not failed
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report:', args.report, flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
