# AI Content Studio — «МяуРкетинг»

Локальная фабрика вертикальных роликов (Reels / VK Клипы / YouTube Shorts) с постоянным AI-персонажем —
шотландским вислоухим котом-маркетологом.

Конвейер: **сценарий → раскадровка → согласование → озвучка → AI-сцены (только где нужно) → автомонтаж FFmpeg →
техпроверка → пакет публикации**. Публикация в соцсети не автоматизирована и выполняется только вручную.

## Установка

Пошаговая инструкция с нуля для Windows: [docs/START.md](docs/START.md).

Нужны Python ≥ 3.10 и FFmpeg (со сборкой libass, есть в стандартных пакетах).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env        # впишите ключи локально, не отправляйте их в чат
studio doctor               # проверка окружения
studio doctor --check-api   # бесплатная проверка подключения и баланса API
```

Фирменный шрифт с кириллицей (например, Montserrat ExtraBold) положите в `assets/fonts/`.
Если папка пуста, используется системный шрифт.

## Быстрый старт

```bash
studio character list                       # референсы кота
studio character approve office_hoodie_laptop   # утвердить после просмотра
studio templates                            # 3 стартовых сценария
studio new --template 01-direct-budget      # создать проект
studio script show episode-001              # раскадровка + смета → script/storyboard.md
studio pipeline episode-001 --mock          # БЕСПЛАТНЫЙ тестовый прогон всего цикла на копии проекта
studio script approve episode-001           # утвердить сценарий (без этого платные операции заблокированы)
studio estimate episode-001                 # смета
studio voice episode-001                    # озвучка (Yandex SpeechKit, кешируется)
studio generate episode-001                 # AI-сцены: смета → подтверждение «да» → генерация
studio assemble episode-001                 # монтаж (бесплатно, можно повторять)
studio qa episode-001                       # техническая проверка
studio approve episode-001                  # ваше утверждение после просмотра
studio package episode-001                  # обложки, тексты, хештеги → publish/
```

Идентификатор эпизода можно сокращать до уникального префикса (`episode-001`).

### Правки и переделки

```bash
studio scene set episode-001 s02 --voiceover "Новый текст" --duration 6
studio scene set episode-001 s04 --prompt "the cat stretches" --generator kling
studio generate episode-001 --regenerate s04    # переделать только одну сцену (новая платная попытка)
studio scene import episode-001 s01 hedra.mp4    # готовый клип из веб-интерфейса (Hedra, Kling и др.)
studio cancel episode-001                        # отменить производство
studio status episode-001 --refresh              # состояние задач, докачка результатов
studio costs [episode-001]                       # бюджет и расходы
studio history [episode-001]                     # журнал действий
```

Любая правка сценария снимает его утверждение. Изменённые сцены перегенерируются, неизменённые
переиспользуются бесплатно.

## Как работать через Claude Code

Пишите задачу обычным текстом, например: «Создай три Reels про ошибки в Яндекс Директе, 30 секунд,
с юмором». Claude Code работает по [CLAUDE.md](CLAUDE.md):
1. пишет сценарии;
2. показывает раскадровку и смету;
3. ждёт вашего утверждения;
4. запускает команды выше.

Платные операции выполняются только после вашего явного подтверждения.

## Архитектура

```
assets/character/       библиотека персонажа: референсы, character.yaml (черты внешности, negative prompt)
assets/templates/       стартовые сценарии
config/studio.yaml      формат видео, субтитры, безопасные зоны, бюджет, модели
config/pricing.yaml     тарифы для сметы (с источником и датой проверки)
config/voices.yaml      голосовые пресеты
projects/<episode>/     script/ images/ audio/ scenes/ subtitles/ work/ output/ publish/ imports/
data/studio.sqlite3     задания генерации, расходы, журнал (не в Git)
src/studio/
  models.py             сценарий и сцены (YAML), валидация
  project.py            проекты и жизненный цикл: draft → approved → voiced → generated → assembled → qa → final_approved → packaged
  integrations/         единый интерфейс адаптеров: kling, hedra, runway, tts (yandex/elevenlabs/manual/mock), mock
  generation/           озвучка с кешем; планировщик и исполнитель генераций (идемпотентность, восстановление)
  editing/              FFmpeg: локальные сцены (карточки, графики, скриншоты, Кен Бёрнс), субтитры, монтаж
  quality/              автоматическая техпроверка + чек-лист ручной проверки
  costs/                бюджет, лимиты, подтверждения
  publishing/           обложки и тексты публикации
```

**Типы сцен:**
- `talking` — говорящий кот: Kling Avatar (фото + аудио) или Hedra;
- `character_motion` — анимация без речи: Kling image-to-video, 5 с, затем локально продлевается «туда-обратно»;
- `screen_demo` / `screenshot` — ваш скриншот: рамки-акценты, плавный зум;
- `infographic` — карточки и анимированные графики (Pillow + FFmpeg, бесплатно);
- `ai_scene` — дополнительная AI-сцена.

Генератор задаётся для каждой сцены: `kling | hedra | runway | local | manual | mock`.

**Защита от лишних расходов:**
- смета показывается до генерации, отправка — только после подтверждения;
- месячный бюджет и лимит на ролик (`config/studio.yaml`);
- одна и та же задача (провайдер, модель, промпт, кадр, аудио) не оплачивается повторно;
- запись о задаче создаётся до запроса к API;
- после сбоя связи задача ищется по `external_task_id`, а не отправляется заново;
- ограничено число платных попыток на сцену.

**Повторная сборка** (`studio assemble`) никогда не обращается к платным API.

**Формат:** 1080×1920, 9:16, 30 fps, H.264 + AAC, MP4, громкость −14 LUFS.

Субтитры вшиваются в видео (ASS) и дополнительно сохраняются в SRT. Отступы задаются в `safe_area`.

## Безопасность

- Ключи хранятся только в `.env` (он в `.gitignore`).
- В логах и выводе диагностики ключи маскируются.
- Используются только официальные API. Для недоступных функций есть ручной импорт.

Подробнее об API, ценах и рисках — в [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Тесты

```bash
pip install -e ".[dev]" && pytest
```
