"""Package a verified full UI recording, Russian narration, subtitles and chapters.

This script does not record the application or generate evidence. It refuses a
partial/check-only pass and publishes artifacts only after the encoded video has
passed validation. Run after the final narration mix; use --overwrite explicitly
to replace an existing docs/video-tour.mp4.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import wave
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / '.local' / 'video-tour' / 'recording'
DOCS = ROOT / 'docs'
VOICE = 'vits-piper-ru_RU-ruslan-medium'
LICENSE = 'https://creativecommons.org/licenses/by-nc-sa/4.0/'
ATTRIBUTION = 'docs/video-tour-audio-license.md'
PLACEHOLDER = 'Точные отметки времени добавляются по результату успешной финальной записи.'
MAX_BYTES = 95 * 1024 * 1024
EXPECTED_CHAPTERS = (
    'СТАРТ', '14', '15', '16 / D9', 'D1', 'D2', 'D3', 'D4',
    'D5', 'D6', 'D7', 'D8', 'D10', 'D11',
    '18.1', '18.2', '18.3', '18.4 / D12', '18.5', 'ФИНИШ',
)
EXPECTED_DOWNLOADS = {
    'team.pdf', 'athlete-count.xlsx', 'competition-protocol.pdf',
    'athlete-history.pdf', 'participation-with-qr.pdf',
    'judging-panel.pdf', 'judging-history.pdf',
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path.name}')
    return value


def number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number.')
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f'{label} must be a finite {"positive" if positive else "nonnegative"} number.')
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path) -> dict:
    process = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams',
         '-show_chapters', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True,
    )
    value = json.loads(process.stdout)
    if not isinstance(value, dict):
        raise ValueError('ffprobe did not return an object.')
    return value


def media_duration(info: dict) -> float:
    try:
        return number(float(info['format']['duration']), 'Media duration', positive=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Media duration is missing or invalid.') from error


def escaped_metadata(value: object) -> str:
    result = str(value).replace('\\', '\\\\')
    for char in ('=', ';', '#'):
        result = result.replace(char, '\\' + char)
    return result.replace('\r', '').replace('\n', '\\\n')


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'


def display_time(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}' if hours else f'{minutes:02}:{seconds:02}'


def validate_evidence(evidence: dict) -> tuple[list[dict], list[dict], list[dict]]:
    if (evidence.get('successful') is not True or evidence.get('recording') is not True
            or evidence.get('partial') is not False):
        raise ValueError('Requires successful=true, recording=true and partial=false from the final UI recording.')
    if evidence.get('pageErrors') != [] or evidence.get('navigationErrors') != []:
        raise ValueError('Browser or server-navigation errors prevent publication.')
    if evidence.get('viewport') != {'width': 1440, 'height': 960}:
        raise ValueError('The recorded viewport must be exactly 1440x960.')
    number(evidence.get('durationSeconds'), 'Evidence duration', positive=True)
    chapters = evidence.get('chapters')
    if not isinstance(chapters, list) or len(chapters) != 20:
        raise ValueError('Exactly 20 completed chapters are required.')
    codes = [chapter.get('code') for chapter in chapters]
    if tuple(codes) != EXPECTED_CHAPTERS:
        raise ValueError('Chapter order/coverage is incomplete; requires all sections including 16 / D9 and 18.4 / D12.')
    covered = {int(match.group(1)) for code in codes for match in re.finditer(r'\bD(\d+)\b', code)}
    if covered != set(range(1, 13)):
        raise ValueError('Every implemented module D1–D12 must be covered.')
    previous = -1.0
    for chapter in chapters:
        timestamp = number(chapter.get('seconds'), 'Chapter timestamp')
        if timestamp <= previous or not isinstance(chapter.get('title'), str) or not chapter['title'].strip():
            raise ValueError('Chapter titles and strictly increasing timestamps are required.')
        previous = timestamp
    events = evidence.get('narrationEvents')
    if not isinstance(events, list) or not events:
        raise ValueError('The final recording must contain narrationEvents.')
    previous = -1.0
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('text'), str) or not event['text'].strip():
            raise ValueError('Every narration event must contain text.')
        timestamp = number(event.get('seconds'), 'Narration timestamp')
        if timestamp < previous:
            raise ValueError('Narration timestamps must be ordered.')
        previous = timestamp
    records = evidence.get('downloads')
    if not isinstance(records, list) or len(records) != 7:
        raise ValueError('Seven actual application downloads are required.')
    if {item.get('file') for item in records} != EXPECTED_DOWNLOADS:
        raise ValueError('The required collection of seven downloaded documents is incomplete.')
    downloads = []
    for record in records:
        filename = record['file']
        if Path(filename).name != filename:
            raise ValueError('A downloaded filename must not contain a path.')
        artifact = RECORDING / 'artifacts' / filename
        if not artifact.is_file() or artifact.stat().st_size <= 100:
            raise ValueError(f'Downloaded document is missing or empty: {filename}')
        if artifact.stat().st_size != record.get('bytes'):
            raise ValueError(f'Downloaded document no longer matches its evidence: {filename}')
        if artifact.suffix == '.pdf':
            with artifact.open('rb') as stream:
                if stream.read(5) != b'%PDF-':
                    raise ValueError(f'Invalid downloaded PDF: {filename}')
        else:
            with ZipFile(artifact) as archive:
                if archive.testzip() is not None or 'xl/workbook.xml' not in archive.namelist():
                    raise ValueError(f'Invalid downloaded XLSX: {filename}')
        downloads.append({'file': filename, 'bytes': artifact.stat().st_size, 'sha256': sha256(artifact)})
    return chapters, events, downloads


def create_subtitles(events: list[dict], index: dict, report: dict, duration: float) -> tuple[str, int]:
    raw_offset = report.get('timestampOffsetSeconds', 0)
    if isinstance(raw_offset, bool) or not isinstance(raw_offset, (int, float)) or not math.isfinite(raw_offset):
        raise ValueError('Narration offset must be finite.')
    offset = float(raw_offset)
    if report.get('voice') != VOICE or report.get('license') != LICENSE:
        raise ValueError('Narration voice/license does not match the documented RUSLAN attribution.')
    if report.get('segments') != len(events):
        raise ValueError('The mixed narration report belongs to a different recording.')
    cues = []
    last_end = -1.0
    for index_number, event in enumerate(events):
        text = event['text']
        cached = index.get(text)
        if not isinstance(cached, dict):
            raise ValueError('The narration duration index is incomplete.')
        length = number(cached.get('duration'), 'Narration clip duration', positive=True)
        start = number(event['seconds'], 'Narration start') + offset
        if start < 0:
            raise ValueError('A narration offset places a subtitle before the recording.')
        end = start + length
        if start < last_end - .1:
            raise ValueError('Narration clips overlap; cannot produce synchronized subtitles.')
        if end > duration + .1:
            raise ValueError('Narration extends past the recorded video.')
        if index_number + 1 < len(events):
            end = min(end, float(events[index_number + 1]['seconds']) + offset - .001)
        end = min(end, duration)
        if end <= start:
            raise ValueError('A subtitle would have an empty interval.')
        safe_text = re.sub(r'\s+', ' ', text).strip().replace('\x00', '')
        wrapped = '\n'.join(textwrap.wrap(safe_text, width=82, break_long_words=False, break_on_hyphens=False))
        cues.append(f'{index_number + 1}\n{srt_time(start)} --> {srt_time(end)}\n{wrapped}\n')
        last_end = start + length
    return '\n'.join(cues), len(cues)


def chapter_metadata(chapters: list[dict], duration: float) -> tuple[str, list[dict]]:
    lines = [
        ';FFMETADATA1',
        'title=ОренКод — подробная демонстрация работающей платформы',
        'artist=ОренКод',
        'language=rus',
        'license=' + escaped_metadata('CC BY-NC-SA 4.0; ' + LICENSE),
        'copyright=' + escaped_metadata('Synthetic Russian narration derived from RUSLAN; CC BY-NC-SA 4.0'),
        'comment=' + escaped_metadata('Voice: ru_RU-ruslan-medium / RUSLAN. Attribution: ' + ATTRIBUTION),
    ]
    summaries = []
    for index_number, chapter in enumerate(chapters):
        # MP4 chapter tracks conventionally begin at zero; include the short
        # genuine lead-in before the first title in the opening chapter.
        recorded_start = number(chapter['seconds'], 'Chapter start')
        start = 0.0 if index_number == 0 else recorded_start
        end = float(chapters[index_number + 1]['seconds']) if index_number + 1 < len(chapters) else duration
        if not 0 <= start < end <= duration + .001:
            raise ValueError('Chapter timestamps are outside the recorded video.')
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        if end_ms <= start_ms:
            raise ValueError('A chapter would have an empty interval.')
        label = f"{chapter['code']} · {chapter['title']}"
        lines.extend(['', '[CHAPTER]', 'TIMEBASE=1/1000', f'START={start_ms}', f'END={end_ms}',
                      'title=' + escaped_metadata(label)])
        summaries.append({'code': chapter['code'], 'title': chapter['title'],
            'startSeconds': round(start, 3), 'recordedStartSeconds': round(recorded_start, 3),
            'endSeconds': round(end, 3), 'time': display_time(start)})
    return '\n'.join(lines) + '\n', summaries


def update_chapter_document(markdown: str, content: str, *, overwrite: bool) -> str:
    count = markdown.count(PLACEHOLDER)
    if count == 1:
        return markdown.replace(PLACEHOLDER, content, 1)
    if count > 1 or not overwrite:
        raise ValueError('Expected a single final-timestamp placeholder; use --overwrite only for a previously packaged video.')
    headings = list(re.finditer(r'(?m)^## Главы[ \t]*$', markdown))
    if len(headings) != 1:
        raise ValueError('Cannot safely identify the single chapter section for --overwrite.')
    start = headings[0].end()
    next_heading = re.search(r'(?m)^##[ \t]+', markdown[start:])
    end = start + next_heading.start() if next_heading else len(markdown)
    # On a repeat packaging run, only the body of this exact public chapter
    # section changes. All surrounding narrative and attribution stay intact.
    return markdown[:start] + '\n\n' + content.rstrip() + ('\n\n' if next_heading else '\n') + markdown[end:]


def verified_output(path: Path, source_duration: float, chapters: list[dict]) -> tuple[dict, dict, dict]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Encoded MP4 exceeds 95 MiB; do not push it. Adjust encoding before publication.')
    info = probe(path)
    video = [stream for stream in info.get('streams', []) if stream.get('codec_type') == 'video']
    audio = [stream for stream in info.get('streams', []) if stream.get('codec_type') == 'audio']
    subtitles = [stream for stream in info.get('streams', []) if stream.get('codec_type') == 'subtitle']
    if len(video) != 1 or video[0].get('codec_name') != 'h264' or video[0].get('codec_tag_string') != 'avc1':
        raise ValueError('Expected exactly one AVC/H.264 video stream.')
    if (video[0].get('width'), video[0].get('height'), video[0].get('pix_fmt')) != (1440, 960, 'yuv420p'):
        raise ValueError('MP4 video must be 1440x960 yuv420p.')
    if (len(audio) != 1 or audio[0].get('codec_name') != 'aac'
            or str(audio[0].get('sample_rate')) != '44100' or audio[0].get('channels') != 1):
        raise ValueError('Expected one mono AAC stream at 44100 Hz.')
    if (len(subtitles) != 1 or subtitles[0].get('codec_name') != 'mov_text'
            or subtitles[0].get('tags', {}).get('language') != 'rus'):
        raise ValueError('Expected one Russian mov_text subtitle stream.')
    actual_duration = media_duration(info)
    if abs(actual_duration - source_duration) > 1.0:
        raise ValueError('Encoded duration differs from the actual source recording by more than one second.')
    actual_chapters = info.get('chapters', [])
    if len(actual_chapters) != len(chapters):
        raise ValueError('Encoded MP4 lost chapters.')
    for actual, expected in zip(actual_chapters, chapters):
        if abs(float(actual['start_time']) - expected['startSeconds']) > .01:
            raise ValueError('Encoded chapter timestamps changed.')
    tags = info.get('format', {}).get('tags', {})
    if LICENSE not in tags.get('license', '') or 'RUSLAN' not in tags.get('comment', '') or ATTRIBUTION not in tags.get('comment', ''):
        raise ValueError('Required RUSLAN license/attribution metadata is missing.')
    return info, video[0], audio[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overwrite', action='store_true', help='Replace an existing verified public MP4 explicitly.')
    args = parser.parse_args()
    output = DOCS / 'video-tour.mp4'
    if output.exists() and not args.overwrite:
        parser.error('docs/video-tour.mp4 already exists; use --overwrite explicitly.')
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            raise FileNotFoundError(f'Required local program not found: {executable}')
    evidence = read_json(RECORDING / 'evidence.json')
    chapters, events, downloads = validate_evidence(evidence)
    report = read_json(RECORDING / 'narration.json')
    duration_index = read_json(ROOT / '.local' / 'video-tour' / 'narration-index.json')
    source = RECORDING / 'video-tour.webm'
    narration = RECORDING / 'narration.wav'
    source_info = probe(source)
    duration = media_duration(source_info)
    source_videos = [stream for stream in source_info.get('streams', []) if stream.get('codec_type') == 'video']
    if len(source_videos) != 1 or (source_videos[0].get('width'), source_videos[0].get('height')) != (1440, 960):
        raise ValueError('Source recording must have one 1440x960 video stream.')
    if abs(duration - float(evidence['durationSeconds'])) > max(4.0, duration * .02):
        raise ValueError('Evidence duration does not match the actual source recording.')
    with wave.open(str(narration), 'rb') as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getframerate() != 22050:
            raise ValueError('Mixed narration must be mono PCM16 at 22050 Hz.')
        narration_duration = stream.getnframes() / stream.getframerate()
    if abs(narration_duration - number(report.get('durationSeconds'), 'Mixed narration duration', positive=True)) > .05:
        raise ValueError('Mixed WAV no longer matches its narration report.')
    subtitles, cue_count = create_subtitles(events, duration_index, report, duration)
    metadata, chapter_summaries = chapter_metadata(chapters, duration)
    markdown_path = DOCS / 'video-tour.md'
    markdown = markdown_path.read_text(encoding='utf-8')
    update_chapter_document(markdown, '', overwrite=args.overwrite)
    attribution = DOCS / 'video-tour-audio-license.md'
    if not attribution.is_file() or 'CC BY-NC-SA 4.0' not in attribution.read_text(encoding='utf-8'):
        raise ValueError('The public RUSLAN attribution document is required.')
    metadata_path = RECORDING / 'chapters.ffmeta'
    metadata_path.write_text(metadata, encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='packaging-', dir=RECORDING) as temporary:
        staging = Path(temporary)
        srt_path = staging / 'video-tour.srt'
        srt_path.write_text(subtitles, encoding='utf-8')
        encoded = staging / 'video-tour.mp4'
        command = [
            'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning', '-stats', '-n',
            '-i', str(source), '-i', str(narration),
            '-f', 'srt', '-i', str(srt_path), '-f', 'ffmetadata', '-i', str(metadata_path),
            '-map', '0:v:0', '-map', '1:a:0', '-map', '2:s:0', '-map_metadata', '3', '-map_chapters', '3',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-threads', '4', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '96k', '-ac', '1', '-ar', '44100', '-af', 'apad',
            '-c:s', 'mov_text', '-metadata:s:a:0', 'language=rus', '-metadata:s:s:0', 'language=rus',
            '-metadata:s:s:0', 'title=Русские субтитры',
            '-metadata', 'license=CC BY-NC-SA 4.0; ' + LICENSE,
            '-metadata', 'comment=Voice: ru_RU-ruslan-medium / RUSLAN. Attribution: ' + ATTRIBUTION,
            '-movflags', '+faststart+use_metadata_tags', '-t', f'{duration:.3f}', str(encoded),
        ]
        print('Encoding the verified full recording with synchronized Russian audio, subtitles and 20 chapters.', flush=True)
        subprocess.run(command, check=True)
        info, video_stream, audio_stream = verified_output(encoded, duration, chapter_summaries)
        audit = {
            'successful': True, 'recording': True, 'partial': False,
            'generatedAt': datetime.now(timezone.utc).isoformat(),
            'sha256': sha256(encoded), 'bytes': encoded.stat().st_size,
            'durationSeconds': round(media_duration(info), 3),
            'sourceSha256': sha256(source), 'sourceDurationSeconds': round(duration, 3),
            'viewport': {'width': 1440, 'height': 960},
            'codecs': {'video': video_stream['codec_name'], 'videoTag': video_stream['codec_tag_string'],
                'pixelFormat': video_stream['pix_fmt'], 'audio': audio_stream['codec_name'],
                'audioSampleRate': int(audio_stream['sample_rate']), 'audioChannels': audio_stream['channels'],
                'subtitles': 'mov_text', 'subtitleLanguage': 'rus'},
            'counts': {'chapters': len(chapters), 'downloads': len(downloads), 'narrationSegments': len(events),
                'subtitleCues': cue_count, 'browserErrors': 0, 'serverNavigationErrors': 0},
            'errors': {'browser': [], 'serverNavigation': []},
            'voice': {'model': VOICE, 'corpus': 'RUSLAN', 'license': 'CC BY-NC-SA 4.0',
                'licenseUrl': LICENSE, 'synthesizedLocally': True,
                'spokenSeconds': number(report.get('spokenSeconds'), 'Spoken duration')},
            'chapters': chapter_summaries, 'downloads': downloads,
        }
        chapter_table = ['| Время | Раздел | Содержание |', '| --- | --- | --- |']
        for chapter in chapter_summaries:
            title = chapter['title'].replace('|', '\\|').replace('\n', ' ')
            chapter_table.append(f"| {chapter['time']} | {chapter['code']} | {title} |")
        chapter_table.append('\nПроверка готового MP4, глав, озвучки и семи скачанных документов: [результаты](audit/video-tour.json).')
        # Preserve any independent documentation edits made during encoding.
        latest_markdown = markdown_path.read_text(encoding='utf-8')
        updated_markdown = update_chapter_document(latest_markdown, '\n'.join(chapter_table), overwrite=args.overwrite)
        audit_directory = DOCS / 'audit'
        audit_directory.mkdir(parents=True, exist_ok=True)
        # All validations have passed; only public, explicitly selected artifacts
        # are written. Raw evidence, credentials, audio cache and local paths stay local.
        os.replace(encoded, output)
        os.replace(srt_path, DOCS / 'video-tour.srt')
        (audit_directory / 'video-tour.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        markdown_path.write_text(updated_markdown, encoding='utf-8')
    print(f'Packaged and verified video-tour.mp4: {audit["bytes"] / 1024 / 1024:.2f} MiB; '
          f'{audit["durationSeconds"]:.2f}s; 20 chapters; 7 downloads; {cue_count} subtitle cues.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
