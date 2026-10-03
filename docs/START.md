# Запуск с нуля: пошаговая инструкция (Windows)

Все команды вводятся в **PowerShell**: Пуск → наберите «PowerShell» → Windows PowerShell.
Вставить строку — правая кнопка мыши, выполнить — Enter.

---

## Часть 1. Программы на компьютере (один раз, ~15 минут)

**1.1.** Установите Python, FFmpeg и Git. По одной команде, если спросит — введите `Y`:
```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git
```

**1.2.** Закройте PowerShell и откройте его заново.

**1.3.** Проверьте установку — каждая команда должна вывести версию:
```powershell
python --version
ffmpeg -version
git --version
```
Если `python` открывает Microsoft Store: Пуск → «Управление псевдонимами выполнения приложений» →
выключите оба `python.exe` → откройте PowerShell заново.

---

## Часть 2. Проект (один раз)

**2.1.** Скачайте проект в папку «Документы»:
```powershell
cd $HOME\Documents
git clone https://github.com/dkaratsapov-web/ai-cat.git
cd ai-cat
git checkout claude/sweet-tesla-o409yu
```
Если откроется окно входа GitHub — войдите аккаунтом с доступом к репозиторию.

**2.2.** Установите проект:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```
В начале строки появится `(.venv)`.
Если ошибка «выполнение сценариев отключено» — выполните
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, введите `Y` и повторите `.venv\Scripts\activate`.

**2.3.** Создайте файл для ключей и проверьте окружение:
```powershell
copy .env.example .env
studio doctor
```
Строки `[WARN]` про ключи и референсы на этом этапе — нормально. `[FAIL]` — пришлите скриншот.

---

## Часть 3. Yandex Cloud — озвучка (~10 минут)

**3.1.** Откройте https://console.yandex.cloud и войдите через Яндекс ID.

**3.2.** Левое меню → **Биллинг** → создайте платёжный аккаунт (физлицо), привяжите карту.
Активируйте стартовый грант, если предложат.

**3.3.** Создайте сервисный аккаунт:
- в консоли откройте каталог `default`;
- **Identity and Access Management** (или поиск «Сервисные аккаунты») → **Создать сервисный аккаунт**;
- имя: `ai-cat`;
- **Добавить роль** → `ai.speechkit-tts.user` → **Создать**.

**3.4.** Создайте ключ:
- откройте созданный аккаунт `ai-cat`;
- вверху **Создать новый ключ** → **Создать API-ключ** → **Создать**;
- **скопируйте секретный ключ** — он показывается один раз.

**3.5.** Вставьте ключ в проект (см. часть 5).

---

## Часть 4. Kling — видео (~10 минут)

**4.1.** Откройте https://kling.ai/dev/api-key → **+ Create a new API Key** → имя `ai-cat` →
**скопируйте ключ** (показывается один раз).

**4.2.** Левое меню → **API Purchase** → вкладка **Video API** → **Trial Package $9.8** (100 units) → **Purchase** → оплата.

**4.3.** Левое меню → **Resource Packages**: пакет должен быть со статусом online.

---

## Часть 5. Куда вставлять ключи

**5.1.** В PowerShell, в папке проекта:
```powershell
cd $HOME\Documents\ai-cat
notepad .env
```

**5.2.** В Блокноте заполните две строки — значение сразу после `=`, без пробелов и кавычек:
```
KLING_API_KEY=ключ_из_Kling
YANDEX_API_KEY=ключ_из_Yandex_Cloud
```
Остальные строки оставьте как есть. **Ctrl+S**, закройте Блокнот.

**5.3.** Проверьте подключение (бесплатно):
```powershell
studio doctor --check-api
```
Должно быть: `Видео: kling — OK` (+ остаток units), `TTS: yandex — OK`.

> Ключи никому не отправляйте и не показывайте на скриншотах. Файл `.env` не попадает в GitHub.

---

## Часть 6. Первый ролик

**6.1.** Посмотрите фото кота: откройте папку `Документы\ai-cat\assets\character\references`,
файл `office_hoodie_laptop.jpg`. Если всё ок — утвердите:
```powershell
studio character approve office_hoodie_laptop
```

**6.2.** Создайте проект ролика и посмотрите сценарий со сметой:
```powershell
studio new --template 01-direct-budget
studio script show episode-001
```

**6.3.** Бесплатная репетиция (заглушки вместо API, проверяет монтаж):
```powershell
studio pipeline episode-001 --mock
```

**6.4.** Настоящий ролик:
```powershell
studio script approve episode-001
studio voice episode-001
studio generate episode-001
studio assemble episode-001
studio qa episode-001
```
- `voice` — озвучка Яндексом (≈ 1 ₽), подтвердите словом `да`;
- `generate` — покажет смету Kling (≈ $0,6), подтвердите `да`, ждать несколько минут;
- `assemble` — монтаж, `qa` — проверка.

**6.5.** Готовое видео: папка `Документы\ai-cat\projects\episode-001-...\output\`.
Посмотрите на телефоне. Если нравится:
```powershell
studio approve episode-001
studio package episode-001
```
Обложки, заголовки, описание и хештеги — в папке `publish`.

---

## Каждый следующий раз

```powershell
cd $HOME\Documents\ai-cat
.venv\Scripts\activate
git pull
```

## Если что-то не так

| Проблема | Что сделать |
|---|---|
| Не нравится голос | Послушайте голоса в демо SpeechKit, поменяйте `voice:` в `config\voices.yaml`, затем `studio voice episode-001 --force` |
| Плохо получилась сцена | `studio generate episode-001 --regenerate s01` (новая платная попытка) |
| Ошибка / FAIL | Пришлите скриншот (без ключей) и номер шага |
