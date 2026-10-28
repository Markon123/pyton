# Источник и атрибуция русской озвучки

Русская озвучка видеодемонстрации «ОренКод» синтезирована локально. Голоса пользователей платформы не записывались; приватные данные и текст не отправлялись внешним речевым сервисам.

Использована нейросетевая модель **ru_RU-ruslan-medium** (VITS/Piper, один русский мужской голос, 22 050 Гц). Источник корпуса — [RUSLAN](https://ruslan-corpus.github.io/). Карточка модели указывает для корпуса [Creative Commons Attribution–NonCommercial–ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/).

Модель получена из [публичного релиза sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-ru_RU-ruslan-medium.tar.bz2). SHA-256 файла `ru_RU-ruslan-medium.onnx`:

```text
3cb25352186ef2ae116f994ca4a06444a07c11187c8aa1c9e3e64aafea99b054
```

Изменение: создана новая синтетическая речь по русскому сценарию демонстрации, технические сокращения раскрыты для произношения, фрагменты размещены по времени реальных действий на экране. Озвучка и демонстрационный видеоматериал с этой дорожкой предназначены для некоммерческой защиты проекта и предоставляются с указанием источника на условиях **CC BY-NC-SA 4.0**. При передаче или публикации сохраняйте эту атрибуцию и ссылку на лицензию.

Для синтеза использованы [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 2.0.1 и фонемизатор [Piper](https://github.com/OHF-Voice/piper1-gpl) 1.8.0. Эти инструменты устанавливаются отдельно в `.local/video-tour/tts-venv`; модель и кэш аудио исключены из Git и не являются зависимостями веб-приложения.

## Оригинальная карточка модели

```text
# Model card for ruslan (medium)

* Language: ru_RU (Russian, Russia)
* Speakers: 1
* Quality: medium
* Samplerate: 22,050Hz

## Dataset

* URL: https://ruslan-corpus.github.io/
* License: https://creativecommons.org/licenses/by-nc-sa/4.0/

## Training

Finetuned from U.S. English lessac voice (medium quality).
```
