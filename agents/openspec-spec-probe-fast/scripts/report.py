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
    if schema.get('minLength', 0) and isinstance(value, str) and not value.strip():
        raise ValueError(f'{path}: blank text')
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

def coverage_ref(report, row):
    if 'ref' in row:
        return row['ref']
    return next(entry['ref'] for entry in report.get('reference_map', []) if entry['id'] == row['ref_id'])


def normalize(report):
    r = copy.deepcopy(report)
    validate(r, json.loads(SCHEMA.read_text()))
    references = r.get('reference_map', [])
    reference_ids = [entry['id'] for entry in references]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError('duplicate reference id')
    if any(not re.fullmatch(r'R-[0-9]+', rid) for rid in reference_ids):
        raise ValueError('reference id must be R-<number>')
    for row in r['coverage']:
        if ('ref' in row) == ('ref_id' in row):
            raise ValueError('coverage requires exactly one of ref and ref_id')
        if 'ref_id' in row and row['ref_id'] not in reference_ids:
            raise ValueError('unknown requirement reference')
        if row['kind'] == 'observation' and 'ref_id' in row:
            raise ValueError('observation requires direct OBS ref')
    if len({f['id'] for f in r['findings']}) != len(r['findings']):
        raise ValueError('duplicate finding id')
    ids = {f['id'] for f in r['findings']}
    for finding in r['findings']:
        if finding['id'] != finding['id'].strip():
            raise ValueError('finding id contains surrounding whitespace')
        if len(set(finding['observation_ids'])) != len(finding['observation_ids']):
            raise ValueError('duplicate observation reference')
    for links in ([c['finding_ids'] for c in r['checks'].values()]
                  + [row['finding_ids'] for row in r['coverage']]):
        if len(set(links)) != len(links):
            raise ValueError('duplicate finding reference')
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
    for finding in r['findings']:
        source_keys = set()
        for source in finding.get('sources', []):
            key = (source['section'], source['path'], source['location'])
            if key in source_keys:
                raise ValueError('duplicate structured source')
            source_keys.add(key)
            for field in ('path', 'location', 'language'):
                if any(c in source.get(field, '') for c in ('\n', '\r', '`')):
                    raise ValueError('source metadata must be a single line without backticks')
            if source.get('excerpt') and len(source['excerpt'].splitlines()) > 15:
                raise ValueError('source excerpt exceeds 15 lines; use a focused excerpt or reference')
    coverage = r['coverage']
    for row in coverage:
        if row.get('in_scope', True) is False and (row['kind'] != 'observation' or row['status'] != 'unchecked' or row['finding_ids']):
            raise ValueError('only unchecked observations without findings may be out of scope')
    keys = [(row['kind'], coverage_ref(r, row), row.get('condition')) for row in coverage]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate coverage entry')
    for row in coverage:
        if (row['kind'] != 'observation') != bool(row.get('condition', '').strip()):
            raise ValueError('each criterion or scenario row requires one condition')
        links = set(row['finding_ids'])
        if links - ids or bool(links) != (row['status'] == 'finding'):
            raise ValueError('invalid coverage finding reference')
    partial = (any(row['status'] == 'unchecked' and row.get('in_scope', True) for row in coverage)
               or not any(row['kind'] in ('criterion', 'scenario') for row in coverage)
               or any(r['checks'][k]['status'] == 'unknown' for k in ('requirements', 'comparison')))
    unusable = (any(r['checks'][k]['status'] != 'passed'
                    for k in ('inputs', 'observations', 'freshness'))
                or any(r['checks'][k]['status'] == 'failed' for k in ('requirements', 'comparison')))
    high_findings = any(f['severity'] == 'high' for f in r['findings'])
    r['verdict'] = ('blocked_high_severity' if high_findings else 'unable_to_review' if unusable else 'partial_review' if partial else 'needs_attention'
                    if r['tests']['status'] == 'failed' else 'passed_with_remarks'
                    if r['findings'] or r['questions'] else 'no_material_findings')
    return r


VERDICTS = {'blocked_high_severity': '🔴 Ревью не пройдено: есть находки высокой важности',
            'partial_review': 'Проверено частично', 'unable_to_review': 'Не удалось выполнить ревью',
            'needs_attention': 'Ревью не пройдено: выполненные тесты завершились ошибкой',
            'passed_with_remarks': '🟢 Ревью пройдено с замечаниями: находок высокой важности нет',
            'no_material_findings': '🟢 Ревью пройдено: существенных замечаний не обнаружено'}
CHECKS = {'inputs': 'Входные данные', 'observations': 'Наблюдения по коду',
          'requirements': 'Требования', 'comparison': 'Сравнение', 'freshness': 'Актуальность входов'}
STATUSES = {'passed': 'Выполнена', 'failed': 'Не выполнена', 'unknown': 'Частично проверено'}
SEVERITIES = {'high': '🔴 Высокая', 'medium': '🟡 Средняя', 'low': '🟢 Низкая'}
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

def question_registry(r):
    """One deterministic list for both the summary count and visible questions."""
    registry = {}
    for f in sorted(r['findings'], key=finding_order):
        if f['decision']['kind'] == 'ask':
            q = ' '.join(f['decision']['question'].split())
            registry.setdefault(q, []).append(f['id'])
    for question in r['questions']:
        registry.setdefault(' '.join(question.split()), [])
    return registry

def display_order(finding):
    return ({'high': 0, 'medium': 1, 'low': 2}[finding['severity']], finding_order(finding))


# Legacy free-text reports remain readable. New reports should use sources for
# precise paths (including spaces) and excerpts; never rewrite fenced code.
PATH_TOKEN = re.compile(r"(?<![\w:/])(?:[\w.@-]+/)*[\w@-]+\.(?:tsx?|jsx?|py|md|json|ya?ml|html|css|sh)(?::[0-9]+(?:[–-][0-9]+)?)?(?![\w.])")


def prose(text, seen=None):
    parts = re.split(r'(```[^\n]*\n.*?```)', text, flags=re.S)
    output = []
    for part in parts:
        if part.startswith('```'):
            output.append(part)
            continue
        # Preserve Markdown links, including URLs, as explicit source lines.
        pattern = r'\[[^\]\n]+\]\([^\)\n]+\)|`[^`\n]+`|' + PATH_TOKEN.pattern
        def reference(match):
            token = match.group(0)
            raw = token.strip('`')
            is_link = token.startswith('[')
            if not is_link and not PATH_TOKEN.fullmatch(raw):
                return token
            key = raw
            if seen is not None and key in seen:
                return '(источник указан выше)'
            if seen is not None:
                seen.add(key)
            label = token if is_link else '`' + raw + '`'
            return '\n\n📄 ' + label + '\n\n'
        output.append(re.sub(pattern, reference, part))
    return '\n'.join(output).strip()


def source_blocks(finding, section, seen):
    lines = []
    for source in finding.get('sources', []):
        if source['section'] != section:
            continue
        ref = source['path'] + (':' + source['location'] if source['location'] else '')
        if ref not in seen:
            if not source['location'] or re.fullmatch(r'[0-9]+(?:[–-][0-9]+)?', source['location']):
                lines += ['📄 `' + ref + '`', '']
            else:
                lines += ['📄 `' + source['path'] + '`', '', 'Место: ' + source['location'], '']
            seen.add(ref)
        if source.get('excerpt'):
            # Choose a fence longer than any backtick run in the actual source.
            longest = max((len(x) for x in re.findall(r'`+', source['excerpt'])), default=0)
            fence = '`' * max(3, longest + 1)
            lines += [fence + source.get('language', ''), source['excerpt'], fence, '']
    return lines


def details_markdown(r):
    order = r['reading_order']
    labels = {'verified': 'соблюдён', 'violated': 'нарушен', 'unknown': 'не подтверждён'}
    lines = [f'# Подробности проверки: {r["change_name"]}', '',
             '[← Итог и находки](report.md)', '', '## Область проверки', '', prose(r['scope']), '',
             '## Порядок чтения', '', f'**{labels[order["status"]]}**. {order["basis"]}', '']
    if order['status'] != 'verified':
        lines += ['Исключить прогон из оценки эффекта порядка чтения.', '']
    lines += ['[Наблюдения по коду](observations.md)', '', '## Список проверок', '']
    for name, check in r['checks'].items():
        lines += [f'### {CHECKS[name]} — {STATUSES[check["status"]]}', '', prose(check['reason']), '']
        if check['finding_ids']:
            lines += ['Находки: ' + ', '.join(check['finding_ids']), '']
    lines += ['### Тесты', '', r['tests']['status'], '', prose(r['tests']['reason']), '', '## Покрытие', '']
    results = {'confirmed': 'Подтверждено', 'finding': 'Есть находка', 'unchecked': 'Не проверено'}
    for i, row in enumerate(r['coverage'], 1):
        lines += [f'### {i}. {row["kind"]} — {results[row["status"]]}', '', prose(coverage_ref(r, row)), '']
        if row.get('condition'):
            lines += [prose(row['condition']), '']
        lines += [prose(row['basis']), '']
        if row.get('in_scope', True) is False:
            lines += ['Вне согласованной области ревью.', '']
        if row['finding_ids']:
            lines += ['Находки: ' + ', '.join(row['finding_ids']), '']
    return '\n'.join(lines)


def markdown(r):
    counts = Counter(f['severity'] for f in r['findings'])
    decisions = Counter(f['decision']['kind'] for f in r['findings'])
    questions = question_registry(r)
    findings = sorted(r['findings'], key=display_order)
    anchors = {f['id']: f'finding-{i}' for i, f in enumerate(findings, 1)}
    lines = [f'# Ревью change: {r["change_name"]}', '', f'**{VERDICTS[r["verdict"]]}**', '',
             f'🔴 Высокая: {counts["high"]} · 🟡 Средняя: {counts["medium"]} · 🟢 Низкая: {counts["low"]}', '',
             f'Готовых правок: {decisions["patch"]}. Открытых вопросов: {len(questions)}.', '']
    if counts['high']:
        lines += ['Блокирующие находки: ' + ', '.join(f['id'] for f in findings if f['severity'] == 'high'), '']
    if len(findings) > 3:
        lines += ['## Указатель находок', '']
        for f in findings:
            lines += [f'- [{f["id"]} · {SEVERITIES[f["severity"]]} · {f["summary"]}](#{anchors[f["id"]]})']
        lines.append('')
    lines += ['## Находки', '']
    labels = {'patch': 'Предложить правку', 'ask': 'Нужно решение владельца',
              'keep': 'Сохранить требование', 'verify': 'Уточнить доказательства'}
    fields = {'question': 'Вопрос', 'impact': 'Как ответ изменит спеку', 'target': 'Файл и раздел',
              'before': 'Сейчас', 'after': 'Предлагается', 'intent_source': 'Источник намерения',
              'basis': 'Основание решения', 'missing': 'Чего не хватает', 'check': 'Как проверить'}
    for f in findings:
        seen = set()
        lines += [f'<a id="{anchors[f["id"]]}"></a>', '',
                  f'### {f["id"]} · {SEVERITIES[f["severity"]]} · {f["summary"]}', '',
                  f'Тип: {CAUSES[f["cause"]]}', '', '**Почему это важно**', '', prose(f['problem']), '',
                  '#### Решение', '', '**' + labels[f['decision']['kind']] + '**', '']
        for key, label in fields.items():
            if key in f['decision']:
                lines += ['**' + label + '**', '', prose(f['decision'][key]), '']
        for heading, key in [('Как описано в исходном change', 'original_change'), ('Как реализовано в коде', 'code')]:
            lines += ['#### ' + heading, '']
            lines += source_blocks(f, key, seen)
            lines += [prose(f[key], seen), '']
        if f['cause'] == 'spec_conflict':
            lines += ['**Условие несовместимости**', '', prose(f['incompatibility']), '']
        evidence = []
        evidence += source_blocks(f, 'evidence', seen)
        earlier = {f['code'].strip(), f['original_change'].strip()}
        for e in f['evidence']:
            if e.strip() in earlier:
                continue
            earlier.add(e.strip())
            formatted = prose(e, seen)
            if formatted.strip() not in ('', '(источник указан выше)'):
                evidence += [formatted, '']
        if evidence:
            lines += ['#### Дополнительные доказательства', '', *evidence]
    if not findings:
        lines += ['В проверенном объёме находок нет.', '']
    if questions:
        lines += ['## Вопросы для решения', '']
        for number, (question, ids) in enumerate(questions.items(), 1):
            if ids:
                links = ', '.join(f'[{fid}](#{anchors[fid]})' for fid in ids)
                lines += [f'{number}. Решение по находке {links}.']
            else:
                lines += [f'{number}. {prose(question)}']
        lines.append('')
    limits = list(r['limitations'])
    for name, check in r['checks'].items():
        if check['status'] != 'passed':
            limits.append(f'{CHECKS[name]}: {check["reason"]}')
    if r['tests']['status'] != 'passed':
        status = {'not_run': 'Не запускались', 'failed': 'Есть ошибки', 'unknown': 'Результат неизвестен'}
        limits.append(f'Тесты — {status[r["tests"]["status"]]}: {r["tests"]["reason"]}')
    if limits:
        lines += ['## Существенные ограничения', '']
        for limitation in dict.fromkeys(limits):
            lines += [prose(limitation), '']
    lines += ['[Подробности проверки и покрытие →](review-details.md)', '']
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
    (run / 'review-details.md').write_text(details_markdown(r))
    # Separate publication metadata: inputs and frozen observations remain immutable.
    from datetime import datetime, timezone
    publication = {'report_generated_at': datetime.now(timezone.utc).isoformat(),
                   'report_json_sha256': runner.digest(args.report),
                   'report_md_sha256': runner.digest(run / 'report.md'),
                   'review_details_sha256': runner.digest(run / 'review-details.md')}
    (run / '_meta/publication.json').write_text(json.dumps(publication, indent=2) + '\n')
    print(r['verdict'])

if __name__=='__main__':
    main()
