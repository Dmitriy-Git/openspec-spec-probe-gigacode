# Установка openspec-spec-probe в GigaCode CLI

Формат адаптирован по https://gitverse.ru/docs/ai/ai-assistant-gigacode/gigacode-cli/subagents (проверено 2026-11-02). Файл регистрации готов для установки. Реальный запуск GigaCode в среде подготовки пакета не выполнен: CLI отсутствует в PATH.

## Какие файлы копировать

Из этого пакета перенесите содержимое `agents/` в `.gigacode/agents/` вашего проекта, а папку `spec-probe/` целиком — в `.gigacode/spec-probe/`. Если каталоги существуют, объедините содержимое, не заменяя остальные файлы проекта.

Получится:

```text
ваш-проект/
  .gigacode/
    agents/
      openspec-spec-probe.md
    spec-probe/
      spec-review-agent.md
      report.schema.json
      evaluation.md
      scripts/
        report.py
```

В `.gigacode/agents/` должен попасть только файл регистрации, а не все Markdown-документы. evaluation.md нужен для пилота, для самого анализа не обязателен. Другие файлы выше нужны для полного цикла с машинной проверкой отчёта. Python 3 использует только стандартную библиотеку.

Откройте GigaCode CLI в корне проекта и `/agents manage`: проверьте наличие openspec-spec-probe. Если текущая сессия не обнаружила новый файл, откройте новую сессию и повторите проверку. `/agents create` не требуется, если агент обнаружен из файла.

## Первый вызов

В чате GigaCode можно попросить:

```text
Вызови именованный субагент openspec-spec-probe (не fork).
Проверь доступность SUPPORT_DIR=.gigacode/spec-probe и сообщи,
какие входы нужны для reverse и compare. Пока не запускай ревью.
```

Это проверка регистрации и чтения комплекта, а не проверка качества анализа.

## Reverse одной заранее подготовленной реализации

Перед вызовом подготовитель должен предоставить пакет одной реализации. Агент сам не запускает openspec apply и не создаёт sandbox. Пакет содержит manifest.json с SHA/хешами, схемой артефактов, путями разрешённых файлов, результатом проверки на утечки и состоянием изоляции; код, diff, связанный неизменённый код, базовые спеки и общие правила. Подробные требования — в spec-review-agent.md. Не копируйте туда Jira или исходный change.

Пример запроса; замените пути и имя реализации своими:

```text
Вызови именованный субагент openspec-spec-probe (не fork).
Передай ему только следующие параметры, без пересказа задачи или change:
MODE=reverse
SUPPORT_DIR=.gigacode/spec-probe
IMPL=impl-a
PACKAGE_DIR=.spec-probe-inputs/impl-a
OUT_DIR=.spec-probe/my-change
```

Для impl-b выполните отдельный вызов с другим PACKAGE_DIR и IMPL. Само размещение пакетов в разных каталогах не ограничивает чтение соседних файлов. Для verified требуется внешнее ограничение файлового доступа и подтверждение его работы. Без него возможен предварительный анализ с isolation=unknown и итогом inconclusive.

## Compare

Сохраните требования локально; MCP Jira не нужен и не включён в allowlist. Подготовьте manifest текущего запуска и JSON SERVICES с base/implementation SHA каждого сервиса. Замените все заполнители, затем отправьте:

```text
Вызови именованный субагент openspec-spec-probe.
MODE=compare
SUPPORT_DIR=.gigacode/spec-probe
CHANGE_PATH=openspec/changes/my-change
PLAN_ROOT=.
CHANGE_SHA=<полный SHA снимка change>
BASELINE_SPECS_SHA=<полный SHA базовых спек до change>
SERVICES=<массив сервисов с path, base SHA и impls: имя -> SHA>
IMPLS=[impl-a, impl-b]
REQUIREMENTS_PATH=.spec-probe-inputs/requirements.md
MANIFEST_PATH=.spec-probe-inputs/manifest.json
REVERSE_DIR=.spec-probe/my-change/reverse
OUT_DIR=.spec-probe/my-change
```

Для store PLAN_ROOT и CHANGE_PATH указывают на репозиторий планирования, а SERVICES — на репозитории сервисов. Результаты: `.spec-probe/my-change/report.json` и `report.md`.

Для повторной ручной проверки отчёта из корня проекта:

```sh
python3 .gigacode/spec-probe/scripts/report.py .spec-probe/my-change/report.json
```

## Что настроено и что остаётся внешним

В файле задан именованный агент, model=inherit, approvalMode=default и явный список файловых инструментов и shell. Shell нужен для проверки хешей, CLI и отчёта; его наличие не создаёт sandbox. Согласно документации, более permissive режим родителя может иметь приоритет над approvalMode агента.

Устанавливаемый агент анализирует предоставленные данные и пишет отчёты. Генерация независимых реализаций, техническая изоляция, подготовка пакетов и окружения тестов остаются внешними этапами. Недоступные обязательные проверки честно дают inconclusive. Конфигурация не обещает полностью автоматический запуск всего процесса одной командой.
