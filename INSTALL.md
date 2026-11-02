# openspec-spec-probe — один субагент

Скопируйте содержимое agents/ в .gigacode/agents/ вашего проекта, объединив каталоги. Файл openspec-spec-probe.md должен находиться непосредственно в agents/, а одноимённая папка со служебными файлами — рядом с ним. Не заменяйте целиком agents/: там могут быть другие субагенты. При обновлении удалите прежний .gigacode/agents/openspec-spec-probe-reverse.md, чтобы GigaCode не регистрировал второй агент. Другие агенты не затрагивайте. Проверьте /agents manage; при необходимости начните новую сессию.

После apply и ручных правок:

> Вызови openspec-spec-probe для change `my-change`. Задача: <Jira>; контекст: <Confluence>. Проверь текущую реализацию, включая незакоммиченные правки. Не передавай содержимое исходного change в стартовый контекст субагента.

Один агент сам выполняет подготовку → восстановление change по коду и требованиям → фиксацию reverse → чтение оригинала → сравнение → отчёт. Других субагентов, пробных реализаций, apply и автоматических повторов нет. Можно указать полный путь change; store и код в src/ могут находиться отдельно. Технические аргументы helper-скриптов определяет агент.

Результат хранится в `<store>/openspec/reviews/<change-id>/<run-id>/`:

```text
report.md
reverse-change/
  proposal.md
  specs/
  design.md
  evidence.md
_meta/
  inputs.json
  reverse-hashes.json
  freshness.json
  report.json
```

`report.md` содержит итог, область проверки, список проверок, находки с сопоставлением reverse/кода/оригинала и при необходимости вопросы для решения. Служебные JSON фиксируют входы, неизменность reverse, актуальность данных и структурированное содержимое отчёта; вручную читать их не требуется. Исходники и спецификации не изменяются. Мастер-спека берётся до sync change.

Один контекст — штатный режим, без обязательной проверки изоляции и без isolation.json. Сначала агент фиксирует reverse, затем читает оригинал. Это процедурная последовательность, а не технически независимый слепой анализ. Существенное загрязнение контекста отмечается в области проверки.

Итоги: «Есть замечания», «Существенных замечаний не обнаружено» в рассмотренном объёме, «Не удалось выполнить ревью» при непригодных входах или невозможности сравнения. Находки не означают сбой проверки. Частичное покрытие описывается предметно и не перекрывает находки. Реальное выполнение обновлённого пакета в GigaCode требует проверки в пользовательской среде.

При обновлении прежняя .gigacode/spec-probe/ больше не используется: после проверки новой установки её можно удалить, если в ней нет ваших дополнительных файлов. Папка examples/ содержит только образцы отчёта и не нужна для установки.

Структура после установки:

```text
.gigacode/agents/
  openspec-spec-probe.md
  openspec-spec-probe/
    report.schema.json
    scripts/
      runner.py
      report.py
```

Минимальный комплект:

```text
INSTALL.md
agents/openspec-spec-probe.md
agents/openspec-spec-probe/report.schema.json
agents/openspec-spec-probe/scripts/runner.py
agents/openspec-spec-probe/scripts/report.py
```

Python 3, без внешних зависимостей. runner.py фиксирует входы и проверяет свежесть; report.py проверяет отчёт и создаёт Markdown. Инструкции обеих фаз находятся в единственном файле агента.

Внутренние команды (аргументы заполняет агент):

```sh
python3 .gigacode/agents/openspec-spec-probe/scripts/runner.py prepare /path/to/store/openspec/changes/my-change --service api=/path/to/src/api --base api=<base-commit> --source /path/to/task-export.md
python3 .gigacode/agents/openspec-spec-probe/scripts/runner.py check /path/to/run
python3 .gigacode/agents/openspec-spec-probe/scripts/report.py /path/to/run/_meta/report.json
```

Для исторической мастер-спеки: --baseline-ref <commit-в-store>. Старый .spec-probe.json с adapter не используется. Старые отчёты не удаляются; отчёты схем 2.0 и 4.0 несовместимы с текущим валидатором 5.0. Обновляйте инструкцию, схему и оба скрипта вместе.

Тесты вне пакета: `python3 -B -m unittest discover -s development/openspec-spec-probe-v4 -v` из корня исходного проекта.
