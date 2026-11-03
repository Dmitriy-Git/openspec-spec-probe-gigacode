# openspec-spec-probe — один субагент

Скопируйте содержимое agents/ в .gigacode/agents/ вашего проекта, объединив каталоги. Файл openspec-spec-probe.md должен находиться непосредственно в agents/, а одноимённая папка со служебными файлами — рядом с ним. Не заменяйте целиком agents/: там могут быть другие субагенты. При обновлении удалите прежний .gigacode/agents/openspec-spec-probe-reverse.md, чтобы GigaCode не регистрировал второй агент. Другие агенты не затрагивайте. Проверьте /agents manage; при необходимости начните новую сессию.

После apply и ручных правок:

> Вызови openspec-spec-probe для change `my-change`. Задача: <Jira>; контекст: <Confluence>. Проверь текущую реализацию, включая незакоммиченные правки. Не передавай содержимое исходного change в стартовый контекст субагента.

Передайте путь к исходному change. Агент фиксирует наблюдения по итоговому коду, сверяет их с требованиями и предлагает правки спецификации. При наличии ссылок на обсуждение и ревизий до и после ревью передайте их вместе с задачей.

Каждый запуск создаёт отдельный каталог внутри переданного change:

```text
openspec/changes/<change-id>/reviews/<run-id>/
  observations.md
  report.md
  _meta/
    inputs.json
    freshness.json
    report.json
```

Читайте report.md для принятия решений и observations.md для проверки фактов о коде. В inputs.json сохраняются версии входов и SHA-256 зафиксированных наблюдений. Каталог reviews/ исключён из состава исходных требований. Для просмотра примера откройте examples/report.md.

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
python3 .gigacode/agents/openspec-spec-probe/scripts/runner.py prepare /path/to/store/openspec/changes/my-change --service api=/path/to/src/api --base api=<base-commit> --source /path/to/task-export.md --rule /path/to/src/api/.gigacode/rules.md
# После записи observations.md и до чтения change:
python3 .gigacode/agents/openspec-spec-probe/scripts/runner.py freeze /path/to/run
# После сопоставления:
python3 .gigacode/agents/openspec-spec-probe/scripts/runner.py check /path/to/run
python3 .gigacode/agents/openspec-spec-probe/scripts/report.py /path/to/run/_meta/report.json
```

Для исторической мастер-спеки: --baseline-ref <commit-в-store>. Старый .spec-probe.json с adapter не используется. Старые отчёты не удаляются; отчёты прежних схем (включая 6.0) сохраняются для чтения; новые прогоны используют схему 7.0. Обновляйте инструкцию, схему и оба скрипта вместе.

Тесты вне пакета: `python3 -B -m unittest discover -s development/openspec-spec-probe-v4 -v` из корня исходного проекта.

Передавайте --rule отдельно для каждого прочитанного файла правил store и сервисов. Freeze обнаруживает последующие изменения наблюдений. Порядок чтения в общем контексте проверяйте по истории сессии.
