"""Build a 270-second narrated montage of the submitted checkpoint-2 version.

Inputs are private, genuine recording artifacts under .local/checkpoint2-270.
The original checkpoint video is verified against its historical Git blob.
Every accelerated cut, retained frame and narration adjustment is audited.
This script never starts the application, modifies its DB, or invents evidence.
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


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local' / 'checkpoint2-270'
RECORDING = LOCAL / 'recording'
DOCS = ROOT / 'docs'
COMMIT = '953b71b3f647cd47d03dae2e12e758b9a0c6d09f'
ORIGINAL_BLOB = 'dfbd4489c6c89ff779da65f71866fcb658b2c270'
ORIGINAL = DOCS / 'checkpoint-2.mp4'
RAW = RECORDING / 'raw-tour.webm'
RATE = 22050
FPS = 25
DURATION = 270.0
LICENSE_URL = 'https://creativecommons.org/licenses/by-nc-sa/4.0/'
ATTRIBUTION = 'docs/video-tour-audio-license.md'
SCENES = (
    ('intro', 8), ('core', 70), ('rating', 25), ('registry', 32),
    ('ministry', 16), ('identity', 24), ('training', 24), ('appeal', 16),
    ('development', 20), ('qr', 16), ('guidejudge', 14), ('outro', 5),
)
CORE_PARTS = (
    (9.0, 27.0, 12.0), (27.0, 45.0, 9.0), (45.0, 61.0, 7.0),
    (61.0, 73.0, 8.0), (73.0, 108.0, 20.0), (108.0, 124.88, 14.0),
)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path.name}')
    return value


def finite(value: object, label: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be numeric.')
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f'{label} must be finite and {"positive" if positive else "nonnegative"}.')
    return result


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def probe(path: Path) -> dict:
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-show_chapters', '-of', 'json', str(path),
    ], text=True))


def duration(info: dict) -> float:
    try:
        value = float(info['format']['duration'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Media duration is unavailable.') from error
    return finite(value, 'Media duration', positive=True)


def video_stream(info: dict) -> dict:
    streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
    if len(streams) != 1:
        raise ValueError('Expected one genuine source video stream.')
    if (streams[0].get('width'), streams[0].get('height')) != (1440, 960):
        raise ValueError('Recording must have the expected 1440x960 viewport.')
    return streams[0]


def ffmpeg(arguments: list[str]) -> None:
    subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning',
                    '-n', *arguments], check=True)


def metadata_escape(value: str) -> str:
    value = value.replace('\\', '\\\\')
    for char in ('=', ';', '#'):
        value = value.replace(char, '\\' + char)
    return value.replace('\r', '').replace('\n', '\\\n')


def clock(seconds: float, srt: bool = False) -> str:
    ms = round(seconds * 1000)
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    sec, millis = divmod(rest, 1000)
    if srt:
        return f'{hours:02}:{minutes:02}:{sec:02},{millis:03}'
    return f'{hours:02}:{minutes:02}:{sec:02}' if hours else f'{minutes:02}:{sec:02}'


def validate_inputs(plan: dict, evidence: dict) -> tuple[list[dict], dict[str, dict]]:
    if plan.get('sourceCommit') != COMMIT or evidence.get('sourceCommit') != COMMIT:
        raise ValueError('The plan and recording must both identify the submitted checkpoint-2 commit.')
    if evidence.get('successful') is not True or evidence.get('errors') != []:
        raise ValueError('A successful historical browser recording with no errors is required.')
    for key in ('pageErrors', 'navigationErrors'):
        if key in evidence and evidence[key] != []:
            raise ValueError('Recorded browser/server errors prevent publication.')
    scenes = plan.get('scenes')
    if not isinstance(scenes, list) or len(scenes) != len(SCENES):
        raise ValueError('The approved twelve-scene plan is required.')
    for scene, (scene_id, budget) in zip(scenes, SCENES):
        if scene.get('id') != scene_id or finite(scene.get('duration'), 'Scene duration', True) != budget:
            raise ValueError('Scene IDs, order and budgets must match the 270-second plan.')
        for field in (('title',) if scene_id == 'core' else ('title', 'text')):
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                raise ValueError(f'Every scene needs nonempty {field}.')
        if scene_id == 'core':
            parts = scene.get('parts')
            if not isinstance(parts, list) or len(parts) != len(CORE_PARTS):
                raise ValueError('Core requires the six separately timed narration/video parts.')
            for part, (start, end, duration_budget) in zip(parts, CORE_PARTS):
                if (finite(part.get('sourceStart'), 'Core source start') != start
                        or finite(part.get('sourceEnd'), 'Core source end', True) != end
                        or finite(part.get('duration'), 'Core part duration', True) != duration_budget
                        or not isinstance(part.get('text'), str) or not part['text'].strip()):
                    raise ValueError('Core part cuts, budgets and narration must match the agreed six-step cycle.')
            if sum(float(part['duration']) for part in parts) != float(scene['duration']):
                raise ValueError('Core part budgets must total the planned seventy seconds.')
    if sum(float(s['duration']) for s in scenes) != DURATION:
        raise ValueError('Scene budgets must total exactly 270 seconds.')
    clips = evidence.get('scenes')
    if not isinstance(clips, list):
        raise ValueError('The recording must include measured scene cuts.')
    cuts = {}
    previous_end = -1.0
    for clip in clips:
        scene_id = clip.get('id')
        if scene_id == 'core':
            continue  # The core always comes from the hash-verified original.
        if scene_id in cuts or scene_id not in {name for name, _ in SCENES}:
            raise ValueError('Unexpected or duplicate scene in recording evidence.')
        start = finite(clip.get('start'), 'Source scene start')
        end = finite(clip.get('end'), 'Source scene end', True)
        if end <= start or start < previous_end - .05:
            raise ValueError('Recorded scene boundaries must be ordered and non-overlapping.')
        previous_end = end
        cuts[scene_id] = {'start': start, 'end': end}
    if set(cuts) != {name for name, _ in SCENES if name != 'core'}:
        raise ValueError('The recording must contain all eleven supplementary scenes.')
    expected_blob = subprocess.check_output([
        'git', 'rev-parse', COMMIT + ':docs/checkpoint-2.mp4',
    ], cwd=ROOT, text=True).strip()
    actual_blob = subprocess.check_output([
        'git', 'hash-object', str(ORIGINAL),
    ], cwd=ROOT, text=True).strip()
    if expected_blob != ORIGINAL_BLOB or actual_blob != expected_blob:
        raise ValueError('The original checkpoint-2 video does not match the submitted historical Git blob.')
    return scenes, cuts


def expand_scenes(scenes: list[dict]) -> list[dict]:
    """Keep twelve public chapters but encode/voice seventeen synchronized units."""
    result = []
    for scene in scenes:
        if scene['id'] == 'core':
            for number, part in enumerate(scene['parts'], 1):
                result.append({**part, 'id': f'core-{number}', 'sceneId': 'core',
                    'partNumber': number, 'title': part.get('title') or scene['title']})
        else:
            result.append({**scene, 'sceneId': scene['id']})
    if len(result) != 17 or sum(float(unit['duration']) for unit in result) != DURATION:
        raise ValueError('Expanded montage must contain seventeen units totalling exactly 270 seconds.')
    return result


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), 'rb') as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (RATE, 1, 2):
            raise ValueError('Narration cache must use 22050 Hz mono PCM16 WAV clips.')
        samples = stream.readframes(stream.getnframes())
        if not samples or not any(samples):
            raise ValueError('A prepared narration clip is empty or entirely silent.')
        return samples


def write_audio(scenes: list[dict], index: dict, staging: Path) -> tuple[Path, list[dict], str]:
    pcm = bytearray(round(DURATION * RATE) * 2)
    voice_details = []
    subtitle_cues = []
    offset = 0.0
    for scene in scenes:
        text = scene['text']
        cached = index.get(text)
        if not isinstance(cached, dict) or not isinstance(cached.get('file'), str):
            raise ValueError(f'Missing prepared narration for scene {scene["id"]}.')
        source = Path(cached['file'])
        samples = read_pcm(source)
        original_duration = len(samples) / (2 * RATE)
        if abs(original_duration - finite(cached.get('duration'), 'Cached narration duration', True)) > .05:
            raise ValueError('Cached narration duration does not match the actual WAV.')
        allowed = float(scene['duration']) - .55
        tempo = max(1.0, original_duration / allowed)
        if tempo > 1.18 + 1e-6:
            raise ValueError(f'Voice for {scene["id"]} is {original_duration:.2f}s; its budget allows '
                             f'{allowed:.2f}s and at most 1.18x. Shorten this narration.')
        if tempo > 1:
            adjusted = staging / f'voice-{scene["id"]}.wav'
            # A tiny speed margin accommodates the overlap-add rounding of atempo.
            actual_tempo = min(1.18, tempo * 1.001)
            ffmpeg(['-i', str(source), '-af', f'atempo={actual_tempo:.8f}',
                    '-ar', str(RATE), '-ac', '1', '-c:a', 'pcm_s16le', str(adjusted)])
            samples = read_pcm(adjusted)
            tempo = actual_tempo
        played = len(samples) / (2 * RATE)
        trimmed = max(0.0, played - allowed)
        if trimmed > .08:
            raise ValueError('Adjusted narration still exceeds its scene budget; shorten the text.')
        if trimmed:
            samples = samples[:math.floor(allowed * RATE) * 2]
            played = len(samples) / (2 * RATE)
        start = offset + .25
        first = round(start * RATE) * 2
        if first + len(samples) > len(pcm):
            raise ValueError('Narration would extend beyond the 270-second montage.')
        pcm[first:first + len(samples)] = samples
        voice_details.append({'scene': scene.get('sceneId', scene['id']),
            'unit': scene['id'], 'part': scene.get('partNumber'), 'startSeconds': round(start, 4),
            'durationSeconds': round(played, 4), 'originalDurationSeconds': round(original_duration, 4),
            'tempo': round(tempo, 6), 'trailingPaddingTrimmedSeconds': round(trimmed, 4)})
        # Cues follow the paragraph order and estimated speech progress within
        # each scene. They are not represented as word-level forced alignment.
        wrapped = textwrap.wrap(re.sub(r'\s+', ' ', text).strip(), width=72,
                                break_long_words=False, break_on_hyphens=False)
        groups = ['\n'.join(wrapped[i:i + 2]) for i in range(0, len(wrapped), 2)]
        total_chars = sum(len(group) for group in groups)
        elapsed = 0.0
        for group in groups:
            length = played * len(group) / total_chars
            subtitle_cues.append(f'{len(subtitle_cues) + 1}\n{clock(start + elapsed, True)} --> '
                                 f'{clock(start + elapsed + length, True)}\n{group}\n')
            elapsed += length
        offset += float(scene['duration'])
    output = staging / 'narration.wav'
    with wave.open(str(output), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm)
    return output, voice_details, '\n'.join(subtitle_cues)


def encode_scenes(scenes: list[dict], cuts: dict[str, dict], staging: Path,
                  raw_duration: float, original_duration: float) -> tuple[Path, list[dict]]:
    details = []
    clip_paths = []
    output_start = 0.0
    for number, scene in enumerate(scenes):
        scene_id = scene['id']
        core = scene.get('sceneId') == 'core'
        if core:
            source = ORIGINAL
            start, end = float(scene['sourceStart']), min(float(scene['sourceEnd']), original_duration)
            recorded_end = end
        else:
            source = RAW
            start, recorded_end = cuts[scene_id]['start'], cuts[scene_id]['end']
            if recorded_end > raw_duration + 1.0:
                raise ValueError('A source scene exceeds the actual raw recording.')
            end = min(recorded_end, raw_duration)
        span = end - start
        if span <= 0:
            raise ValueError('A recorded scene contains no available video.')
        budget = float(scene['duration'])
        # Short supplementary cuts are slowed by at most 2x, keeping browser
        # transitions readable before retaining any remaining final-frame time.
        speed = span / budget if span >= budget else 1.0 / min(2.0, budget / span)
        played_span = span / speed
        freeze = max(0.0, budget - played_span)
        destination = staging / f'{number:02}-{scene_id}.mp4'
        filters = (f'setpts=(PTS-STARTPTS)/{speed:.10f},fps={FPS},'
                   'scale=1440:960:flags=lanczos,setsar=1,'
                   f'tpad=stop_mode=clone:stop_duration={budget:.6f}')
        print(f'Encoding historical scene {scene_id}: {span:.2f}s -> {budget:.2f}s '
              f'({speed:.3f}x; hold {freeze:.2f}s).', flush=True)
        ffmpeg(['-ss', f'{start:.6f}', '-t', f'{span:.6f}', '-i', str(source),
                '-map', '0:v:0', '-an', '-sn', '-vf', filters,
                '-frames:v', str(round(budget * FPS)), '-c:v', 'libx264',
                '-preset', 'veryfast', '-crf', '25', '-pix_fmt', 'yuv420p',
                '-threads', '4', '-video_track_timescale', '12800', str(destination)])
        encoded_info = probe(destination)
        if abs(duration(encoded_info) - budget) > .041:
            raise ValueError('A scene was not encoded to its exact planned frame count.')
        clip_paths.append(destination)
        details.append({'id': scene_id, 'scene': scene.get('sceneId', scene_id),
            'part': scene.get('partNumber'), 'title': scene['title'],
            'source': 'submitted-checkpoint-2.mp4' if core else 'historical-browser-recording',
            'sourceStartSeconds': round(start, 6), 'sourceEndSeconds': round(end, 6),
            'recordedEndSeconds': round(recorded_end, 6), 'sourceDurationSeconds': round(span, 6),
            'outputStartSeconds': round(output_start, 6), 'outputEndSeconds': round(output_start + budget, 6),
            'outputDurationSeconds': budget, 'playbackSpeed': round(speed, 8),
            'slowedBy': round(max(1.0, 1.0 / speed), 8),
            'retainedLastFrameSeconds': round(freeze, 6)})
        output_start += budget
    listing = staging / 'clips.ffconcat'
    listing.write_text('ffconcat version 1.0\n' + ''.join(
        "file '" + str(p.resolve()).replace("'", "'\\''") + "'\n" for p in clip_paths), encoding='utf-8')
    return listing, details


def chapters(scenes: list[dict], target: Path) -> list[dict]:
    entries = [';FFMETADATA1',
        'title=ОренКод — второй чекпоинт за 270 секунд',
        'artist=ОренКод',
        'source_commit=' + COMMIT,
        'license=' + metadata_escape('CC BY-NC-SA 4.0; ' + LICENSE_URL),
        'comment=' + metadata_escape('Historical checkpoint-2 montage, original UI; accelerated cuts. '
                                   'Russian voice: RUSLAN / ruslan-medium; attribution: ' + ATTRIBUTION),
    ]
    output = []
    start = 0.0
    for scene in scenes:
        end = start + float(scene['duration'])
        entries += ['', '[CHAPTER]', 'TIMEBASE=1/1000', f'START={round(start * 1000)}',
                    f'END={round(end * 1000)}', 'title=' + metadata_escape(scene['title'])]
        output.append({'id': scene['id'], 'title': scene['title'],
                       'startSeconds': start, 'endSeconds': end, 'time': clock(start)})
        start = end
    target.write_text('\n'.join(entries) + '\n', encoding='utf-8')
    return output


def public_downloads(evidence: dict) -> list[dict]:
    values = evidence.get('downloads', [])
    if not isinstance(values, list):
        raise ValueError('Download evidence must be a list.')
    output = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Download evidence entries must be objects.')
        name = item.get('file', item.get('name'))
        if not isinstance(name, str) or Path(name).name != name or not name:
            raise ValueError('Public download evidence must contain only a basename.')
        count = item.get('bytes')
        if isinstance(count, bool) or not isinstance(count, int) or count <= 100:
            raise ValueError('Downloaded files must have their actual nonempty sizes.')
        result = {'file': name, 'bytes': count}
        artifact = RECORDING / 'artifacts' / name
        if artifact.exists():
            if artifact.stat().st_size != count:
                raise ValueError('A downloaded artifact changed after recording.')
            result['sha256'] = sha256(artifact)
        output.append(result)
    return output


def check_output(path: Path, expected_chapters: list[dict]) -> tuple[dict, dict, dict]:
    if path.stat().st_size >= 95 * 1024 * 1024:
        raise ValueError('MP4 exceeds the 95 MiB publication limit.')
    info = probe(path)
    video = video_stream(info)
    if video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p':
        raise ValueError('The final video must be H.264 / yuv420p.')
    if video.get('r_frame_rate') != '25/1' or int(video.get('nb_frames', 0)) != 6750:
        raise ValueError('The montage must contain exactly 6750 frames at 25 fps.')
    if abs(duration(info) - DURATION) > .041:
        raise ValueError('The final montage must be exactly 270 seconds.')
    if abs(float(video.get('duration', 0)) - DURATION) > .041:
        raise ValueError('The actual video stream must be exactly 270 seconds.')
    audio = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    if (len(audio) != 1 or audio[0].get('codec_name') != 'aac'
            or str(audio[0].get('sample_rate')) != '44100' or audio[0].get('channels') != 1):
        raise ValueError('One audible mono AAC narration stream at 44100 Hz is required.')
    if abs(float(audio[0].get('duration', 0)) - DURATION) > .05:
        raise ValueError('Audio must cover the complete 270-second output.')
    subtitles = [s for s in info.get('streams', []) if s.get('codec_type') == 'subtitle']
    if (len(subtitles) != 1 or subtitles[0].get('codec_name') != 'mov_text'
            or subtitles[0].get('tags', {}).get('language') != 'rus'):
        raise ValueError('Russian MP4 subtitles are required.')
    actual = info.get('chapters', [])
    if len(actual) != len(expected_chapters):
        raise ValueError('The final MP4 lost one or more of its twelve chapters.')
    for found, expected in zip(actual, expected_chapters):
        if (abs(float(found['start_time']) - expected['startSeconds']) > .01
                or abs(float(found['end_time']) - expected['endSeconds']) > .05):
            raise ValueError('MP4 chapter boundaries no longer match the planned montage.')
    tags = info.get('format', {}).get('tags', {})
    if tags.get('source_commit') != COMMIT or LICENSE_URL not in tags.get('license', ''):
        raise ValueError('Historical source or voice license metadata was lost.')
    return info, video, audio[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    output = DOCS / 'checkpoint-2-270.mp4'
    if output.exists() and not args.overwrite:
        parser.error('The public video already exists; use --overwrite explicitly.')
    for program in ('ffmpeg', 'ffprobe', 'git'):
        if not shutil.which(program):
            raise FileNotFoundError(f'Required local tool: {program}')
    plan = read_json(LOCAL / 'plan.json')
    evidence = read_json(RECORDING / 'evidence.json')
    scenes, cuts = validate_inputs(plan, evidence)
    units = expand_scenes(scenes)
    index = read_json(LOCAL / 'narration-index.json')
    downloads = public_downloads(evidence)
    raw_info, original_info = probe(RAW), probe(ORIGINAL)
    video_stream(raw_info)
    video_stream(original_info)
    raw_duration, original_duration = duration(raw_info), duration(original_info)
    if abs(original_duration - 124.88) > .05:
        raise ValueError('The verified original checkpoint recording has an unexpected duration.')
    attribution = DOCS / 'video-tour-audio-license.md'
    if not attribution.exists() or 'CC BY-NC-SA 4.0' not in attribution.read_text(encoding='utf-8'):
        raise ValueError('The public Russian voice attribution document is required.')
    with tempfile.TemporaryDirectory(prefix='package-270-', dir=LOCAL) as folder:
        staging = Path(folder)
        audio_path, voice, subtitle_text = write_audio(units, index, staging)
        subtitle_path = staging / 'checkpoint-2-270.srt'
        subtitle_path.write_text(subtitle_text, encoding='utf-8')
        metadata_path = staging / 'chapters.ffmeta'
        chapter_list = chapters(scenes, metadata_path)
        listing, cut_details = encode_scenes(units, cuts, staging, raw_duration, original_duration)
        encoded = staging / 'checkpoint-2-270.mp4'
        print('Joining twelve historical scenes with Russian narration, subtitles and chapters.', flush=True)
        ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(listing), '-i', str(audio_path),
                '-f', 'srt', '-i', str(subtitle_path), '-f', 'ffmetadata', '-i', str(metadata_path),
                '-map', '0:v:0', '-map', '1:a:0', '-map', '2:s:0', '-map_metadata', '3', '-map_chapters', '3',
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '96k', '-ar', '44100', '-ac', '1',
                '-af', 'loudnorm=I=-18:TP=-1.5:LRA=11', '-c:s', 'mov_text',
                '-metadata:s:a:0', 'language=rus', '-metadata:s:s:0', 'language=rus',
                '-metadata:s:s:0', 'title=Русские субтитры',
                '-movflags', '+faststart+use_metadata_tags', '-t', '270', str(encoded)])
        info, video, audio = check_output(encoded, chapter_list)
        # Decode the complete delivered streams, not just their container headers.
        decoded = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                        '-i', str(encoded), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                        check=True, capture_output=True, text=True)
        if decoded.stderr.strip():
            raise ValueError('The final delivered streams contain decoding errors.')
        audit = {
            'successful': True, 'sourceCommit': COMMIT,
            'sourceCommitUrl': 'https://github.com/Markon123/pyton/tree/' + COMMIT,
            'originalCheckpointVideo': {'file': 'checkpoint-2.mp4', 'gitBlob': ORIGINAL_BLOB,
                'sha256': sha256(ORIGINAL), 'durationSeconds': original_duration,
                'cutStartSeconds': 9.0, 'cutEndSeconds': 124.88},
            'historicalBrowserRecording': {'sha256': sha256(RAW), 'durationSeconds': raw_duration,
                'successful': True, 'errors': []},
            'editing': 'Historical UI montage with six separately timed core steps; supplementary cuts are '
                       'slowed by at most 2x or accelerated to fit, retaining a final frame only when needed.',
            'sha256': sha256(encoded), 'bytes': encoded.stat().st_size,
            'durationSeconds': duration(info), 'resolution': {'width': 1440, 'height': 960},
            'codecs': {'video': video['codec_name'], 'audio': audio['codec_name'],
                'audioSampleRate': int(audio['sample_rate']), 'audioChannels': audio['channels'],
                'subtitles': 'mov_text', 'subtitleLanguage': 'rus'},
            'counts': {'chapters': len(scenes), 'downloads': len(downloads),
                'sourceCuts': len(cut_details), 'voiceSegments': len(voice), 'subtitleCues': subtitle_text.count(' --> '),
                'browserErrors': 0},
            'voice': {'model': 'vits-piper-ru_RU-ruslan-medium', 'corpus': 'RUSLAN',
                'license': 'CC BY-NC-SA 4.0', 'licenseUrl': LICENSE_URL,
                'synthesizedLocally': True, 'clips': voice,
                'normalization': 'loudnorm I=-18 LUFS, TP=-1.5 dBTP, LRA=11'},
            'subtitleTiming': 'Estimated speech progress within each narrated scene; not word-level alignment.',
            'chapters': chapter_list, 'sourceCuts': cut_details, 'downloads': downloads,
            'validation': {'completeAvDecode': True, 'historicalGitBlobMatched': True},
            'generatedAt': datetime.now(timezone.utc).isoformat(),
        }
        public_audit = DOCS / 'audit' / 'checkpoint-2-270.json'
        public_audit.parent.mkdir(parents=True, exist_ok=True)
        # Only explicitly selected deliverables leave the private workspace.
        os.replace(encoded, output)
        os.replace(subtitle_path, DOCS / 'checkpoint-2-270.srt')
        public_audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Packaged checkpoint-2-270.mp4: {audit["durationSeconds"]:.3f}s; '
          f'{audit["bytes"] / 1024**2:.2f} MiB; 12 chapters; source {COMMIT[:7]}.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'270-second packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
