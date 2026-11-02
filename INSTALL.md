# openspec-spec-probe — один субагент

Скопируйте agents/openspec-spec-probe.md в .gigacode/agents/, а spec-probe/ — в .gigacode/spec-probe/. При обновлении удалите прежний .gigacode/agents/openspec-spec-probe-reverse.md, чтобы GigaCode не регистрировал второй агент. Другие агенты не затрагивайте. Проверьте /agents manage; при необходимости начните новую сессию.

После apply и ручных правок:

> Вызови openspec-spec-probe для change `my-change`. Задача: <Jira>; контекст: <Confluence>. Проверь текущую реализацию, включая незакоммиченные правки. Не передавай содержимое исходного change в стартовый контекст субагента.

Один агент сам выполняет подготовку → восстановление change по коду и требованиям → фиксацию reverse → чтение оригинала → сравнение → отчёт. Других субагентов, пробных реализаций, apply и автоматических повторов нет. Можно указать полный путь change; store и код в src/ могут находиться отдельно. Технические аргументы helper-скриптов определяет агент.

Результат: <store>/openspec/reviews/<change-id>/<run-id>/ с inputs.json, reverse-change/, reverse-hashes.json, isolation.json, freshness.json, report.json/report.md. Исходники и спецификации не изменяются. База кода учитывает коммиты и рабочие правки; мастер-спека берётся до sync change.

**Ограничение одного контекста:** запрет чтения оригинала до фиксации reverse задаётся инструкцией, а не sandbox. Агент не заявляет техническую независимость. checks.isolation=unknown; итог по текущему строгому валидатору — inconclusive, с сохранением полезных находок. Отсутствие sandbox больше не блокирует выполнение. Если оригинал уже попал в контекст, это явно отмечается. Реальное выполнение в GigaCode в среде разработки не проверено.

Минимальный комплект:

```text
INSTALL.md
agents/openspec-spec-probe.md
spec-probe/report.schema.json
spec-probe/scripts/runner.py
spec-probe/scripts/report.py
```

Python 3, без внешних зависимостей. runner.py фиксирует входы и проверяет свежесть; report.py проверяет отчёт и создаёт Markdown. Инструкции обеих фаз находятся в единственном файле агента.

Внутренние команды (аргументы заполняет агент):

```sh
python3 .gigacode/spec-probe/scripts/runner.py prepare /path/to/store/openspec/changes/my-change --service api=/path/to/src/api --base api=<base-commit> --source /path/to/task-export.md
python3 .gigacode/spec-probe/scripts/runner.py check /path/to/run
python3 .gigacode/spec-probe/scripts/report.py /path/to/run/report.json
```

Для исторической мастер-спеки: --baseline-ref <commit-в-store>. Старый .spec-probe.json с adapter не используется. Старые отчёты не удаляются; отчёты схемы 2.0 несовместимы с текущим валидатором 4.0.

Тесты вне пакета: `python3 -B -m unittest discover -s development/openspec-spec-probe-v4 -v` из корня исходного проекта.
