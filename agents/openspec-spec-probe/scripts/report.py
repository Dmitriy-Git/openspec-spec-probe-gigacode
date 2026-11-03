"""Validate the bundled schema subset and derive report outputs; standard library only."""
import argparse
from collections import Counter
import copy
import json
import re
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / 'report.schema.json'

def validate(value, schema, path='$'):
    # This is deliberately not a general JSON Schema engine. All assertions used
    # in the bundled schema are implemented here; unknown keywords fail closed.
    supported = {'$schema','$id','title','type','const','enum','minLength','minItems',
                 'properties','required','additionalProperties','items','anyOf'}
    if set(schema) - supported:
        raise ValueError(f'{path}: unsupported schema keywords')
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                validate(value, candidate, path)
                return
            except ValueError:
                pass
        raise ValueError(f'{path}: no allowed variant')
    types = {'object':dict,'array':list,'string':str,'boolean':bool,'null':type(None)}
    if 'type' in schema and type(value) is not types[schema['type']]:
        raise ValueError(f'{path}: expected {schema["type"]}')
    if 'const' in schema and value != schema['const']:
        raise ValueError(f'{path}: wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: invalid enum')
    for key in ('minLength','minItems'):
        if key in schema and len(value) < schema[key]:
            raise ValueError(f'{path}: too short')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - value.keys():
            raise ValueError(f'{path}: missing required fields')
        if schema.get('additionalProperties') is False and value.keys() - props.keys():
            raise ValueError(f'{path}: unexpected fields')
        for key in value.keys() & props.keys():
            validate(value[key], props[key], f'{path}.{key}')
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            validate(item, schema['items'], f'{path}[{i}]')

def normalize(report):
    r = copy.deepcopy(report)
    validate(r, json.loads(SCHEMA.read_text()))
    if len({f['id'] for f in r['findings']}) != len(r['findings']):
        raise ValueError('duplicate finding id')
    ids = {f['id'] for f in r['findings']}
    for check in r['checks'].values():
        if set(check['finding_ids']) - ids:
            raise ValueError('unknown finding reference')
    for finding in r['findings']:
        allowed = {'implementation_defect': {'keep'}, 'spec_gap': {'patch', 'ask'},
                   'spec_conflict': {'patch', 'ask'}, 'intentional_change_candidate': {'ask'},
                   'insufficient_evidence': {'verify'}}
        if finding['decision']['kind'] not in allowed[finding['cause']]:
            raise ValueError('decision incompatible with finding cause')
        if (finding['cause'] == 'spec_conflict') != bool(finding.get('incompatibility', '').strip()):
            raise ValueError('spec_conflict alone requires an incompatibility witness')
    coverage = r['coverage']
    keys = [(row['kind'], row['ref'], row.get('condition')) for row in coverage]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate coverage entry')
    for row in coverage:
        if (row['kind'] != 'observation') != bool(row.get('condition', '').strip()):
            raise ValueError('each criterion or scenario row requires one condition')
        links = set(row['finding_ids'])
        if links - ids or bool(links) != (row['status'] == 'finding'):
            raise ValueError('invalid coverage finding reference')
    partial = (any(row['status'] == 'unchecked' for row in coverage)
               or not any(row['kind'] in ('criterion', 'scenario') for row in coverage)
               or any(r['checks'][k]['status'] == 'unknown' for k in ('requirements', 'comparison')))
    unusable = (any(r['checks'][k]['status'] != 'passed'
                    for k in ('inputs', 'observations', 'freshness'))
                or any(r['checks'][k]['status'] == 'failed' for k in ('requirements', 'comparison')))
    r['verdict'] = ('unable_to_review' if unusable else 'partial_review' if partial else 'needs_attention'
                    if r['findings'] or r['questions'] or r['tests']['status'] == 'failed'
                    else 'no_material_findings')
    return r


VERDICTS = {'partial_review': 'Проверено частично', 'unable_to_review': 'Не удалось выполнить ревью',
            'needs_attention': 'Есть замечания',
            'no_material_findings': 'Существенных замечаний не обнаружено'}
CHECKS = {'inputs': 'Входные данные', 'observations': 'Наблюдения по коду',
          'requirements': 'Требования', 'comparison': 'Сравнение', 'freshness': 'Актуальность входов'}
STATUSES = {'passed': 'Выполнена', 'failed': 'Не выполнена', 'unknown': 'Частично проверено'}
SEVERITIES = {'high': 'высокая', 'medium': 'средняя', 'low': 'низкая'}
CAUSES = {'implementation_defect': 'дефект реализации', 'spec_gap': 'пробел change',
          'spec_conflict': 'противоречие', 'intentional_change_candidate': 'требуется решение',
          'insufficient_evidence': 'требуется проверка'}


def cell(value):
    return value.replace('|', '&#124;').replace('\n', '<br>')


def finding_order(finding):
    identifier = finding['id']
    match = re.fullmatch(r'F-([0-9]+)', identifier)
    if match:
        return (0, int(match[1]), identifier)
    return (1, 0, identifier)

def markdown(r):
    counts = Counter(f['severity'] for f in r['findings'])
    decisions = Counter(f['decision']['kind'] for f in r['findings'])
    questions = set(r['questions']) | {f['decision']['question'] for f in r['findings'] if f['decision']['kind'] == 'ask'}
    order = r['reading_order']
    order_labels = {'verified': 'соблюдён', 'violated': 'нарушен', 'unknown': 'не подтверждён'}
    order_note = ('Порядок подтверждён по указанной истории.' if order['status'] == 'verified'
                  else 'Исключить прогон из оценки эффекта порядка чтения.')
    lines = [f'# Ревью change: {r["change_name"]}', '', '## Итог', '',
             f'**{VERDICTS[r["verdict"]]}**', '',
             f'**Порядок чтения — {order_labels[order["status"]]}**. {order["basis"]} {order_note}', '',
             f'Находок: {len(r["findings"])} (высокая важность: {counts["high"]}, '
             f'средняя: {counts["medium"]}, низкая: {counts["low"]}).', '',
             f'Готовых правок: {decisions["patch"]}. Открытых вопросов: {len(questions)}.', '',
             '[Наблюдения по коду](observations.md)', '',
             '## Область проверки', '', r['scope'], '']
    if r['limitations']:
        lines += ['Границы рассмотренного объёма:', '', *['- ' + x for x in r['limitations']], '']
    lines += ['## Список проверок', '',
              '| Проверка | Что проверено | Результат | Связанные находки |',
              '|---|---|---|---|']
    for name, c in r['checks'].items():
        lines += [f'| {CHECKS[name]} | {cell(c["reason"])} | {STATUSES[c["status"]]} | '
                  f'{", ".join(c["finding_ids"]) or "—"} |']
    test_status = {'passed': 'Пройдены', 'failed': 'Есть ошибки',
                   'not_run': 'Не запускались', 'unknown': 'Результат неизвестен'}
    lines += [f'| Тесты | {cell(r["tests"]["reason"])} | {test_status[r["tests"]["status"]]} | — |',
              '', '## Покрытие', '', '| Тип | Сценарий, критерий или наблюдение | Условие | Результат | Основание | Находки |',
              '|---|---|---|---|---|---|']
    kinds = {'criterion': 'Критерий', 'scenario': 'Сценарий', 'observation': 'Наблюдение'}
    results = {'confirmed': 'Подтверждено', 'finding': 'Есть находка', 'unchecked': 'Не проверено'}
    for row in r['coverage']:
        lines.append('| ' + ' | '.join(map(cell, [kinds[row['kind']], row['ref'], row.get('condition', '—'), results[row['status']],
                                                row['basis'], ', '.join(row['finding_ids']) or '—'])) + ' |')
    lines += ['', '## Находки', '']
    for f in sorted(r['findings'], key=finding_order):
        lines += [f'### {f["id"]}. {f["summary"]}', '',
                  f'Важность: {SEVERITIES[f["severity"]]}  ', f'Тип: {CAUSES[f["cause"]]}', '']
        for heading, key in [('Проблема', 'problem'),
                             ('Как реализовано в коде', 'code'), ('Как описано в исходном change', 'original_change')]:
            lines += [f'#### {heading}', '', f[key], '']
        if f['cause'] == 'spec_conflict':
            lines += ['**Условие несовместимости** — ' + f['incompatibility'], '']
        decision = f['decision']
        labels = {'patch':'Предложить правку', 'ask':'Запросить решение владельца',
                  'keep':'Сохранить требование', 'verify':'Уточнить доказательства'}
        fields = {'target':'Файл и раздел', 'before':'Сейчас', 'after':'Предлагается',
                  'intent_source':'Источник намерения', 'basis':'Основание', 'question':'Вопрос', 'impact':'Как ответ изменит спеку',
                  'missing':'Чего не хватает', 'check':'Как проверить'}
        lines += ['#### Решение по спецификации', '', labels[decision['kind']], '']
        for key, label in fields.items():
            if key in decision:
                lines += [f'**{label}** — {decision[key]}', '']
        for obs in f['observation_ids']:
            lines += [f'[{obs}](observations.md#{obs.lower()})', '']
        lines += ['Доказательства:', '', *['- ' + e for e in f['evidence']], '']
    if not r['findings']:
        lines += ['В проверенном объёме находок нет.', '']
    if r['questions']:
        lines += ['## Вопросы для решения', '', *['- ' + q for q in r['questions']], '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args = parser.parse_args()
    try:
        r = normalize(json.loads(args.report.read_text()))
        run = args.report.resolve().parent.parent
        if args.report.resolve().parent.name != '_meta':
            raise ValueError('expected <run>/_meta/report.json')
        import importlib.util
        spec = importlib.util.spec_from_file_location('probe_runner', Path(__file__).with_name('runner.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        inputs = json.loads((run / '_meta/inputs.json').read_text())
        if r['schema_version'] != inputs['schema_version']:
            raise ValueError('input and report versions differ')
        if r['run_id'] != inputs['run_id']:
            raise ValueError('report run_id differs from inputs')
        fresh = runner.freshness(run)
        if fresh['status'] != 'fresh':
            r['checks']['freshness'] = {'status': 'failed', 'reason': '; '.join(fresh['reasons']), 'finding_ids': []}
        notes = run / 'observations.md'
        if notes.is_file():
            anchors = re.findall(r'^### (OBS-[0-9]+)\s*$', notes.read_text(), re.MULTILINE)
            if len(set(anchors)) != len(anchors):
                raise ValueError('duplicate observation ID')
            covered = {row['ref'] for row in r['coverage'] if row['kind'] == 'observation'}
            if fresh['status'] == 'fresh' and covered != set(anchors):
                raise ValueError('coverage must include every frozen observation exactly once')
            if fresh['status'] == 'fresh' and any(
                set(f['observation_ids']) - set(anchors) for f in r['findings']
            ):
                raise ValueError('unknown observation ID')
        r = normalize(r)
    except (ValueError, OSError) as error:
        parser.exit(2, f'Invalid report: {error}\n')
    args.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    (run / 'report.md').write_text(markdown(r))
    print(r['verdict'])

if __name__=='__main__':
    main()
