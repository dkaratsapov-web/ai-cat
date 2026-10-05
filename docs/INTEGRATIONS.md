# Аудит интеграций (этап 1)

Дата аудита: 2026-10-02. Прямой доступ к сайтам документации из среды разработки был закрыт сетевой политикой,
поэтому источники такие:
- **Kling**: официальные страницы `kling.ai/document-api/...` (llms.txt-версия, снимок от 2026-09-20).
- **Hedra, Runway, ElevenLabs**: официальные SDK на GitHub (hedra-labs/hedra-python, runwayml/sdk-python,
  elevenlabs/elevenlabs-python, коммиты сентября 2026).
- **Цены Hedra / Runway / ElevenLabs**: фрагменты официальных страниц, найденные поиском. В `config/pricing.yaml`
  они помечены `verified: false`.

**Перед первой платной генерацией** выполните `studio doctor --check-api` (запросы баланса бесплатные) и сверьте
цены в личных кабинетах.

## Kling AI — основной генератор ✅

| Что | Статус | Детали |
|---|---|---|
| Базовый URL | ✅ | `https://api-singapore.klingai.com`. Старый `api.klingai.com` заменён. |
| Авторизация | ✅ | API Key → `Authorization: Bearer <key>` (для всех моделей). Пара AK/SK → JWT HS256 (`iss`, `exp`, `nbf`) — только для API «старого» стандарта. Поддерживаются оба варианта. |
| Image-to-Video | ✅ | `POST /image-to-video/kling-2.6`: `contents[]` (prompt, first_frame, last_frame), `settings` (audio, resolution, duration 5/10), `options.external_task_id`. Отдельного `negative_prompt` в этом API нет, поэтому ограничения дописываются в текст промпта. |
| Статус задач | ✅ | `GET /tasks?task_ids=…` или `?external_task_ids=…`. Поиск по нашему `external_task_id` используется для восстановления после сбоя. |
| Говорящий персонаж | ✅ API / ⚠️ коты | `POST /v1/videos/avatar/image2video` с полями image, sound_file (base64, ≤5 МБ, 2–300 с), prompt, mode std/pro. Один платный вызов даёт говорящее видео. **Работа с мордой животного официально не подтверждена** — первым платным тестом проверяем именно это. |
| Lip-sync | ✅ API, не подключён | Двухшаговый: identify-face → advanced-lip-sync. Сейчас не нужен (Avatar дешевле и проще), добавим при необходимости. |
| Нативный звук | ✅ | `settings.audio=native`, только 1080p. Не используем: голос делаем отдельно для постоянства. |
| Баланс | ✅ | `GET /account/costs` — бесплатный, QPS ≤ 1, остаток обновляется с задержкой до 12 ч. |
| Цены | ✅ | 1 Unit = $0.14. Kling 2.6 без звука: 0.3 u/с (720p), 0.5 u/с (1080p). Avatar: 0.4 / 0.8 u/с. Lip Sync: 0.5 u за 5 с. |
| Кредиты подписки = кредиты API? | ❌ не подтверждено | Вторичные источники пишут, что API resource packages покупаются отдельно (от $9.80 за 100 units). **Подписочные кредиты не считаем API-кредитами**, пока это не подтвердит кабинет. |
| Коммерческое использование | ❓ | В найденной документации API не описано. Проверьте условия в кабинете или в Terms. |
| Хранение результатов | ✅ | Ссылки живут 30 дней. Система скачивает результат сразу. |

## Hedra — запасной вариант для говорящего кота ⚠️ экспериментально

- API v3: `https://api.hedra.com/v3`, `Authorization: Bearer <key_id>:<secret>`.
- Схема: `POST /files` (загрузка) → `POST /models/hedra-character-3` с заголовком `Idempotency-Key`, который защищает от двойной оплаты → `GET /jobs/{id}/status` → `GET /jobs/{id}`.
- Поддерживаются 9:16, 540p/720p/1080p и аудио до 600 с.
- ❓ Цена (неофициально): $0.025–0.0625 за секунду. Нужна ли подписка для API — данные противоречат друг другу.
- ❓ Животные: в маркетинговых материалах Hedra говорящие коты есть, но формальной спецификации нет.
- Если API недоступен: сделайте ролик в веб-интерфейсе Hedra и импортируйте его командой `studio scene import <id> <scene> файл.mp4`.

## Runway — сложные сцены, вне MVP ⚠️

- `https://api.dev.runwayml.com`, заголовок `X-Runway-Version: 2024-11-06`.
- Задача: `POST /v1/image_to_video` (gen4_turbo / gen4.5, ratio `720:1280`). Статус: `GET /v1/tasks/{id}`. Баланс: `GET /v1/organization`.
- ❓ Цена: $0.01 за кредит; gen4_turbo — 5 кредитов/с, gen4.5 — 12 кредитов/с.
- MCP: есть официальный `runwayml/runway-api-mcp-server`. Для конвейера используем REST-адаптер, MCP можно подключить к Claude Code отдельно.

## Yandex SpeechKit — основная озвучка ✅

- Выбран вместо ElevenLabs: сильный русский язык, оплата в рублях российской картой. Через API у Kling русской озвучки нет — его TTS поддерживает только `zh` и `en`.
- **API v3 (REST)**: `POST https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis`, заголовок `Authorization: Api-Key <ключ сервисного аккаунта>`.
  - Тело: `text`, `hints[]` (`voice`, `role`, `speed`, `pitchShift`), `outputAudioSpec`, `unsafeMode`.
  - Ответ: поток JSON с `audioChunk.data` (base64) и пословными `wordTimings`, по ним строятся субтитры.
  - Источник: официальные спецификации `github.com/yandex-cloud/cloudapi` (`yandex/cloud/ai/tts/v3`) и SDK `yandex-speechkit`.
- ✅ Цена (официальная страница, подтверждено владельцем 2026-10-03): модель `general` в API v3 — **0,1626 ₽ за запрос** с НДС; `livetts` — 0,25 ₽. Длинные тексты — по 250 символов с округлением вверх. Ролик (5 фраз) ≈ **0,8 ₽**, 30 роликов с переозвучками ≈ **30–50 ₽ в месяц**.
- Бесплатного тарифа нет, есть стартовый грант при регистрации в Yandex Cloud. Его размер уточните в консоли.
- Бесплатного API-метода для проверки баланса нет: баланс смотрите в консоли (Биллинг). Ключ проверяется первой озвучкой.
- Голоса и амплуа (`role`) зависят от голоса. Подбирайте их в демо SpeechKit и задавайте в `config/voices.yaml`.

## ElevenLabs — альтернативная озвучка ✅

- `POST /v1/text-to-speech/{voice_id}/with-timestamps`, заголовок `xi-api-key`.
- Ответ содержит посимвольные тайминги, по ним строятся точные субтитры.
- Для связной интонации между фрагментами используются `previous_text` / `next_text`.
- Настройки голоса: stability, similarity_boost, style, use_speaker_boost, speed.
- Остаток символов: `GET /v1/user/subscription` (бесплатно).
- ❓ Русский язык заявлен для eleven_v3 (74 языка). Для multilingual_v2 подтвердите тестом — скорее всего поддерживается.
- ❓ Коммерческое использование: только на платных тарифах. На Free нужна атрибуция и коммерция запрещена.

## Смета одного ролика (~30 с, шаблоны из `assets/templates/scripts`)

| Операция | Объём | Оценка |
|---|---|---|
| Kling Avatar std, хук + призыв | ~8–10 с | $0.45–0.56 |
| Kling 2.6 720p, 1 сцена движения | 5 с | $0.21 |
| Yandex SpeechKit | ~5 запросов | ≈ 0,8 ₽ |
| Монтаж, карточки, графики, субтитры | локально | $0 |
| **Итого** | | **≈ $0.7–0.8 на ролик**, 30 роликов ≈ $21–24 в месяц, озвучка — копейки |

Перегенерации учитываются отдельно: лимит — `budget.max_attempts_per_scene` попыток на сцену.
При 1080p и режиме `pro` стоимость примерно удваивается.

## Риски

1. **Avatar может не распознать морду кота.** План Б: Hedra Character-3 или Kling i2v + lip-sync.
   Последний вариант — говорящие сцены без синхронизации губ: голос за кадром поверх анимации.
2. **Соотношение сторон берётся из первого кадра.** Сейчас вертикальный кадр вырезается из квадратного референса.
   Для лучшего качества утвердите отдельные вертикальные референсы 9:16.
3. **Дрейф внешности.** Каждая AI-сцена генерируется от утверждённого фото, а не только по тексту. Якорь внешности и
   negative prompt добавляются автоматически. Финальная проверка — только глазами.
4. **Цены и API меняются.** Тарифы вынесены в `config/pricing.yaml`, адаптеры изолированы.

## Higgsfield (исследование, 2026-10-05; адаптер ещё не подключён)

Проверено по официальному Python SDK `higgsfield-client` 0.2.0 (PyPI, автор Higgsfield, репозиторий
github.com/higgsfield-ai/higgsfield-client) и официальному JS SDK github.com/higgsfield-ai/higgsfield-js.
Сайт и docs.higgsfield.ai из облачной среды недоступны — параметры моделей ниже взяты из поисковой выдачи
по open.higgsfield.ai и должны быть сверены с документацией перед подключением.

**Протокол (из кода SDK — проверено):**
- База: `https://api.higgsfield.ai`. Авторизация: заголовок `Authorization: Key <API_KEY>:<API_SECRET>`
  (в SDK — переменные `HF_KEY` или `HF_API_KEY` + `HF_API_SECRET`; ключи — в cloud.higgsfield.ai).
- Запуск: `POST /<модель>` с JSON-аргументами → `{request_id, status_url, cancel_url}`.
  Вебхук — параметр `?hf_webhook=<url>`.
- Статус и результат: `GET /requests/<id>/status` → `status`: queued | in_progress | completed | failed | nsfw | canceled;
  при completed — `video.url` / `images[].url`. По failed и nsfw кредиты возвращаются (README JS SDK).
- Отмена: `POST /requests/<id>/cancel` (только пока queued).
- Загрузка своих файлов: `POST /files/generate-upload-url` `{content_type}` → `{public_url, upload_url, upload_headers}`,
  затем PUT байтов на `upload_url`; в генерацию передаётся `public_url`.

**Модели (по выдаче — сверить):**
- `bytedance/seedance-2.5/image-to-video`: `image_url` (обяз.), `prompt`, `duration` 4–30 с (по умолч. 5),
  `resolution` 480p | 720p | 1080p, `end_image_url`, `output_format` mp4|mov, `generate_audio` (по умолч. **true** —
  нам ставить false: озвучка своя, а звук, по обзорам, удорожает).
- Seedance 2.0, Kling 3.0, Wan — доступны по обзорам; точные id не проверены.
- Собственные: `/v1/image2video/dop` (DoP, `model`, `prompt`, `input_images`, `motions`);
  `/v1/speak/higgsfield` (Speak v2: `input_image` + `input_audio` только WAV, `quality`, `duration`) —
  говорящее видео по фото и аудио. **Работает ли на коте (не человеке) — неизвестно, нужен тест.**

**Деньги и права (по обзорам):** API — отдельный кошелёк в USD, пополнение от $5, без подписки, оплата за генерацию;
подписка на сайте и API — разные продукты. Ориентиры цены: Seedance 2.5 ≈ $0.21/с, Seedance 2.0 ≈ $0.14/с,
Kling 3.0 ≈ $0.084/с, Wan 3.0 ≈ $0.05/с. По условиям использования права на результат у пользователя,
коммерческое использование не ограничено (проверить актуальные Terms).
