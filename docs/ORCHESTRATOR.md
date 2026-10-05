# AI Content Director Orchestrator — архитектура (на ревью, не реализовано)

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

## Что добавить (план)
1. **Director Brief → сценарий.** `briefs/<project>.yaml` в формате директора (project, status: approved, goal, character,
   scenes[id, location, asset, duration, generator, motion], review_rules). Команда `studio brief import <file>`:
   проверяет `status: approved`, сопоставляет `asset` с библиотекой кадров, переводит `motion` в промпт без творческих
   добавок (только обязательные «ears stay folded, static camera»), создаёт проект. Неоднозначность → вопрос, не догадка.
2. **Статусы сцены:** `draft → approved → generating → generated → review → revise → final`
   (`studio scene status <ep>`; монтаж финала — только из сцен `final`).
3. **Стоп после каждой генерации** (режим `--review`): сгенерировать → сохранить `scenes/sNN/generation_vN.mp4` →
   превью (контактный лист кадров + mp4 540p) → статус `review` → остановиться.
4. **Ревью директора:** `studio review <ep> s04 --file review.md` (или текст). Сохраняется в `scenes/s04/review.md`;
   статус `revise` (+ правки промпта/кадра на утверждение) или `final`. Переделывается только указанная сцена.
5. **Версии:** `scenes/sNN/` — `source.png`, `prompt.txt`, `generation_v1.mp4`, `generation_v2.mp4`, `review.md`, `final.mp4`;
   старые версии не удаляются.
6. **Журнал расходов CSV:** `studio costs --csv` → `date,project,scene,provider,model,duration,cost,status`;
   перед каждой генерацией: сцена, провайдер, оценка, потрачено за месяц, «продолжить?».
7. **Финальный QC через директора:** `studio package` собирает файл + превью + список сцен с таймкодами для ревью.

## Higgsfield MCP
Официальный MCP подходит как ручной инструмент поверх. Для конвейера оставляем API: контроль денег, версий,
повторов и истории — у нас. По обзорам MCP тратит кредиты подписки сайта, а не API-кошелёк — сверить перед подключением.

## Dry-run
Весь цикл без денег: `studio brief import … --dry-run` → смета → `studio pipeline <ep> --mock` (настоящие кадры,
тестовый голос, заглушки вместо AI) → превью. Платное — только после «утверждаю» владельца.
