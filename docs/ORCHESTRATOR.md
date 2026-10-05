# AI Content Director Orchestrator + Director Bridge (реализовано, dry-run)

Роли: **ChatGPT — контент-директор** (концепция, бриф, ревью). **Claude Code — технический продюсер**
(API, генерация, версии, деньги, монтаж, хранение). **Владелец** — передаёт брифы и ревью, утверждает платное.
Kling и Higgsfield — только генераторы. Claude не меняет сюжет, персонажа, стиль, CTA и структуру без утверждения.

## Что уже есть в `studio` (работает, проверено на реальных генерациях)
| Требование | Где |
|---|---|
| Ключи только в `.env`, в чат не просить | `config.py`, `doctor` |
| Kling API: `Authorization: Bearer <key>`, база `api-singapore.klingai.com` | `integrations/kling.py` |
| Higgsfield API: `Authorization: Key <id>:<secret>` — **сверено по официальным SDK** и подтверждено реальной генерацией | `integrations/higgsfield.py`, `docs/INTEGRATIONS.md` |
| Генератор выбирается на уровне сцены (`generator: kling | higgsfield`) | сценарий YAML |
| Смета до запуска, подтверждение, лимиты месяц/ролик, лимит попыток | `costs/budget.py`, `generation/runner.py` |
| Кеш: та же сцена (кадр+промпт+длительность+аудио) не оплачивается повторно | ключ задачи в `runner.py` |
| Точечная перегенерация одной сцены | `studio generate <ep> --scenes s04 --regenerate s04` |
| Журнал задач и расходов (SQLite) | `studio jobs`, `studio costs`, `studio history` |
| Очередь под лимит одновременных задач (Kling 1303) | `runner.py` |
| Монтаж локально (FFmpeg): озвучка, субтитры, музыка с приглушением, переходы, плашки, обложка | `editing/*` |
| Предпросмотр без денег (настоящие кадры, тестовый голос) | `studio pipeline <ep> --mock` |
| 20 правил качества | `CLAUDE.md` |

## Реализовано (модуль `studio.director`, папка `director_bridge/`)
Цель — убрать ручное копирование между контент-директором и Claude. Деньги и финальные творческие решения — за владельцем.

| Шаг | Команда | Что происходит |
|---|---|---|
| Бриф | `studio brief import briefs/<p>.yaml [--dry-run]` | только `status: approved`; asset → approved_assets; locked-поля → `director/lock.json`; `director/director_notes.md` |
| Ревью сценария | `studio director review <ep> [--mock\|--manual]` | раскадровка (кадры сцен) + тексты → JSON `script_review` |
| Генерация 1 сцены | `studio generate <ep> --scenes s04 --review` | `scenes/s04/generation_vN.mp4`, стоп, `qc/identity_sheet.jpg` |
| QC кота | `studio idqc <ep> s04 --pass\|--fail --notes ...` | вердикт ставит человек; директор даёт pass только при high confidence |
| Ревью сцены | `studio director review-scene <ep> s04` | кадры клипа + референс → `scenes/s04/review.md`, статус review/revise |
| Применить | `studio review apply <ep> s04 [--approve] [--override]` | показывает правку промпта; платная перегенерация — только отдельной командой со сметой |
| Финал | `studio director review-final <ep>` | пакет: preview.mp4, contact_sheet.jpg, scene_map.json, subtitles.srt, script.txt, director_brief.yaml, costs.csv |
| Без OpenAI | `--manual` / нет ключа | `MANUAL.md` + пакет для ручной вставки в ChatGPT; ответ — `studio review import <ep> <target> --file answer.json` |
| Чат | `studio director chat <ep> --open` · `studio director ask <ep> "вопрос"` | лента Владелец / Директор / Claude в `director/chat.html`; ask — платный запрос после «да» |
| Живой чат | `studio chat <ep>` | http://127.0.0.1:8765: пишете Директору / Claude / Обоим; Claude — локальный Claude Code (`claude -p`), платные команды ему запрещены |
| Claude без установки | кнопки «Отправить Claude» / «Получить ответы Claude» или `studio chat <ep> --share` / `--pull` | переписка ходит через репозиторий; ответы Claude — `projects/<ep>/director/claude_replies.jsonl` |
| Связь | `studio director ping` | бесплатно: ключ и модель; после «да» — один крошечный запрос |
| Прочее | `studio director status\|sync\|export-review-package`, `studio scene-status`, `studio assets --export`, `studio costs --csv` | |

Статусы: `draft → approved → generating → generated → review → (revise | manual_review) → final`.
`final` запрещён, если директор вернул revise на текущую версию или QC кота текущей версии не `pass`
(владелец может снять запрет `--override`). `assemble --final` — только когда все сцены `final`.

OpenAI Responses API (`POST /v1/responses`, Bearer, `json_schema` strict, `previous_response_id` — отдельная
переписка на эпизод в `director_bridge/conversations/`). Видео API не принимает — шлём кадры. Ключ `OPENAI_API_KEY`
только в `.env`, в логи не пишется; ключи Kling/Higgsfield в OpenAI не уходят. Каждый вызов OpenAI — после «да».

## Higgsfield MCP
Официальный MCP подходит как ручной инструмент поверх. Для конвейера оставляем API: контроль денег, версий,
повторов и истории — у нас. По обзорам MCP тратит кредиты подписки сайта, а не API-кошелёк — сверить перед подключением.

## Dry-run
Весь цикл без денег: `studio brief import … --dry-run` → смета → `studio pipeline <ep> --mock` (настоящие кадры,
тестовый голос, заглушки вместо AI) → превью. Платное — только после «утверждаю» владельца.
