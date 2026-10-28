"""Synthesize and align Russian narration for the isolated video walkthrough.

Run with .local/video-tour/tts-venv/bin/python. Models and dependencies are
optional recording tools, not application requirements. See docs/video-tour.md.
No text, credentials or application data is sent to an external speech service.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import wave


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / '.local' / 'video-tour'
MODEL_NAME = 'vits-piper-ru_RU-ruslan-medium'
MODEL_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/'
             'tts-models/vits-piper-ru_RU-ruslan-medium.tar.bz2')
LICENSE_URL = 'https://creativecommons.org/licenses/by-nc-sa/4.0/'
SAMPLE_RATE = 22050
SPEED = 1.0


def normalized_text(text: str) -> str:
    """Expand short technical labels for an intelligible Russian voice."""
    replacements = {
        'ФСП ID': 'эф эс пэ ай ди',
        'ФСП': 'эф эс пэ',
        'OpenID Connect': 'оупен ай ди коннект',
        'OIDC': 'оупен ай ди коннект',
        'PKCE': 'пи кей си и',
        'PDF': 'пэ дэ эф',
        'XLSX': 'экс эль эс икс',
        'DOCX': 'док икс',
        'CSV': 'си эс ви',
        'JSON': 'джейсон',
        'JWT': 'джей дабл ю ти',
        'API': 'эй пи ай',
        'QR': 'кью ар',
        'HTTPS': 'эйч ти ти пи эс',
        'userinfo': 'сведения пользователя',
        'ID token': 'идентификационный токен',
        'DEBUG': 'режим разработки',
        'ОренКод': 'Оренкод',
        '→': ', затем ',
        '·': '. ',
        '×': 'коэффициент ',
    }
    for source, target in replacements.items():
        if source.isascii() and source.isalnum():
            text = re.sub(r'\b' + re.escape(source) + r'\b', target, text)
        else:
            text = text.replace(source, target)
    return re.sub(r'\s+', ' ', text).strip()


def json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def wave_duration(path: Path) -> float:
    with wave.open(str(path), 'rb') as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
            raise ValueError(f'Expected mono PCM16 WAV: {path.name}')
        return stream.getnframes() / stream.getframerate()


def load_events(path: Path) -> tuple[dict, list[dict]]:
    evidence = json.loads(path.read_text(encoding='utf-8'))
    events = evidence.get('narrationEvents', [])
    if not isinstance(events, list) or not events:
        raise ValueError('Evidence must contain a non-empty narrationEvents list.')
    for event in events:
        if not isinstance(event.get('text'), str) or not event['text'].strip():
            raise ValueError('Each narration event requires non-empty text.')
        if not isinstance(event.get('seconds'), (int, float)) or event['seconds'] < 0:
            raise ValueError('Each narration event requires a nonnegative timestamp.')
    return evidence, events


def attribution(model_dir: Path, output: Path) -> None:
    model_card = model_dir / 'MODEL_CARD'
    text = (
        '# Атрибуция озвучки видеозаписи ОренКод\n\n'
        'Озвучка синтезирована локально; записи голоса пользователя не использовались.\n\n'
        '- Движок: sherpa-onnx (Apache-2.0), модель VITS/Piper.\n'
        '- Модель: ru_RU-ruslan-medium, русский мужской голос.\n'
        f'- Дистрибутив модели: {MODEL_URL}\n'
        '- Корпус: RUSLAN, https://ruslan-corpus.github.io/.\n'
        f'- Лицензия корпуса, указанная в model card: CC BY-NC-SA 4.0 — {LICENSE_URL}\n'
        '- Создана новая синтетическая русская озвучка текста демонстрации.\n'
        '- Аудиодорожка распространяется с указанием источника и на условиях '
        'CC BY-NC-SA 4.0; предназначена для некоммерческой демонстрации.\n\n'
        '## Оригинальная карточка модели\n\n'
    )
    if model_card.exists():
        text += model_card.read_text(encoding='utf-8')
    output.write_text(text, encoding='utf-8')


def synthesize(events: list[dict], model_dir: Path, cache_dir: Path, index_path: Path) -> dict:
    import sherpa_onnx
    from piper.phonemize_espeak import EspeakPhonemizer

    model_file = model_dir / 'ru_RU-ruslan-medium.onnx'
    for required in (model_file, model_dir / 'tokens.txt', model_dir / 'espeak-ng-data'):
        if not required.exists():
            raise FileNotFoundError(f'Missing local voice resource: {required}')
    cache_dir.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    pending: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for event in events:
        text = event['text']
        if text in seen:
            continue
        seen.add(text)
        spoken = normalized_text(text)
        digest = hashlib.sha256((MODEL_NAME + '\n' + str(SPEED) + '\n' + spoken).encode()).hexdigest()
        target = cache_dir / f'{digest}.wav'
        if target.exists():
            index[text] = {'file': str(target.resolve()), 'duration': round(wave_duration(target), 4)}
        else:
            pending.append((text, target, spoken))
    if pending:
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model_file), lexicon='', tokens=str(model_dir / 'tokens.txt'),
            noise_scale=0.667,
            noise_scale_w=0.8, length_scale=1.0,
        )
        model = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=2, debug=False, provider='cpu')
        config = sherpa_onnx.OfflineTtsConfig(model=model, max_num_sentences=2, silence_scale=0.2)
        if not config.validate():
            raise ValueError('Invalid local Russian TTS configuration.')
        voice = sherpa_onnx.OfflineTts(config)
        phonemizer = EspeakPhonemizer()
        for number, (text, target, spoken) in enumerate(pending, 1):
            generation = sherpa_onnx.GenerationConfig()
            generation.sid = 0
            generation.speed = SPEED
            generation.phoneme_codepoints = [
                [ord(char) for char in sentence]
                for sentence in phonemizer.phonemize(voice.lang or 'ru', spoken)
            ]
            audio = voice.generate('', generation)
            if not len(audio.samples) or audio.sample_rate != SAMPLE_RATE:
                raise ValueError('Russian TTS produced invalid audio.')
            if not sherpa_onnx.write_wave(str(target), audio.samples, audio.sample_rate):
                raise OSError(f'Cannot save synthesized audio: {target.name}')
            duration = round(wave_duration(target), 4)
            index[text] = {'file': str(target.resolve()), 'duration': duration}
            json_write(index_path, index)
            print(f'Synthesized {number}/{len(pending)}: {duration:.2f}s; {target.name[:12]}', flush=True)
    json_write(index_path, index)
    attribution(model_dir, LOCAL / 'audio-attribution.md')
    return index


def mix(evidence: dict, events: list[dict], index: dict, output: Path, offset: float) -> dict:
    import numpy as np

    scheduled = []
    last_end = 0.0
    for event in sorted(events, key=lambda item: item['seconds']):
        text = event['text']
        if text not in index:
            raise ValueError('Narration cache is incomplete; synthesize before mixing.')
        clip = Path(index[text]['file'])
        with wave.open(str(clip), 'rb') as stream:
            if stream.getframerate() != SAMPLE_RATE or stream.getnchannels() != 1 or stream.getsampwidth() != 2:
                raise ValueError(f'Unexpected WAV format: {clip.name}')
            samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype(np.float32) / 32768
        start = float(event['seconds']) + offset
        if start < 0:
            raise ValueError('Negative narration timestamp after offset.')
        if start < last_end - 0.1:
            raise ValueError(f'Narration events overlap by {last_end - start:.2f}s; record with narration pacing enabled.')
        last_end = start + len(samples) / SAMPLE_RATE
        scheduled.append((int(round(start * SAMPLE_RATE)), samples, event))
    duration = max(float(evidence.get('durationSeconds', 0)) + 0.5, last_end + 0.5)
    result = np.zeros(int(duration * SAMPLE_RATE) + 1, dtype=np.float32)
    for start, samples, event in scheduled:
        result[start:start + len(samples)] += samples
    pcm = (np.clip(result, -0.98, 0.98) * 32767).astype('<i2')
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(pcm.tobytes())
    report = {
        'voice': MODEL_NAME, 'sampleRate': SAMPLE_RATE, 'channels': 1,
        'segments': len(scheduled), 'durationSeconds': round(duration, 3),
        'spokenSeconds': round(sum(len(s) / SAMPLE_RATE for _, s, _ in scheduled), 3),
        'timestampOffsetSeconds': offset, 'license': LICENSE_URL,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
    }
    json_write(output.with_suffix('.json'), report)
    print('Mixed synchronized Russian narration:', output.name, json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=LOCAL / 'recording' / 'evidence.json')
    parser.add_argument('--model-dir', type=Path, default=LOCAL / 'tts-model' / MODEL_NAME)
    parser.add_argument('--cache-dir', type=Path, default=LOCAL / 'narration-cache')
    parser.add_argument('--index', type=Path, default=LOCAL / 'narration-index.json')
    parser.add_argument('--mix', action='store_true', help='Mix existing clips on final recording timestamps.')
    parser.add_argument('--output', type=Path, default=LOCAL / 'recording' / 'narration.wav')
    parser.add_argument('--offset', type=float, default=0.0, help='Optional measured video/audio start offset.')
    parser.add_argument('--sample', action='store_true', help='Synthesize one Russian sentence without a recording.')
    args = parser.parse_args()
    if args.sample:
        evidence = {}
        events = [{'seconds': 0, 'kind': 'sample', 'text': 'ОренКод. Единая платформа спортивного программирования. Покажем сборную региона, отчёты и работу пользователей.'}]
    else:
        evidence, events = load_events(args.evidence)
    if args.mix:
        if not evidence.get('successful') or not evidence.get('recording'):
            raise ValueError('Mix requires a successful final recorded UI walkthrough.')
        index = json.loads(args.index.read_text(encoding='utf-8'))
        mix(evidence, events, index, args.output, args.offset)
    else:
        index = synthesize(events, args.model_dir, args.cache_dir, args.index)
        print(f'Ready: {len({e["text"] for e in events})} unique speech segments, index {args.index.name}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ImportError) as error:
        print(f'Narration failed: {error}', file=sys.stderr)
        raise SystemExit(1)
