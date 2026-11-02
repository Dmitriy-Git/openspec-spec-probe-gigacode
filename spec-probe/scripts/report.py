"""Validate the bundled schema subset and derive report outputs; standard library only."""
import argparse
from collections import Counter
import copy
import json
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
    incomplete = (any(c['status'] != 'passed' for c in r['checks'].values())
                  or bool(r['limitations'])
                  or any(f['cause'] == 'insufficient_evidence' for f in r['findings'])
                  or r['tests']['status'] == 'unknown')
    r['verdict'] = ('inconclusive' if incomplete else 'needs_attention'
                    if r['findings'] or r['tests']['status'] == 'failed' else 'no_material_findings')
    return r


def markdown(r):
    lines = [f'# Review: {r["run_id"]}', '', f'Итог: **{r["verdict"]}**', '',
             r['scope'], '', '## Проверки', '']
    lines += [f'- {name}: {c["status"]} — {c["reason"]}' for name, c in r['checks'].items()]
    lines += ['', f'Тесты: {r["tests"]["status"]} — {r["tests"]["reason"]}', '', '## Находки', '']
    for f in sorted(r['findings'], key=lambda x: {'high':0,'medium':1,'low':2}[x['severity']]):
        lines += [f'### {f["id"]}: {f["summary"]}', '', f'{f["severity"]} / {f["cause"]}', '',
                  *['- ' + e for e in f['evidence']], '', f['recommendation'], '']
    if not r['findings']:
        lines += ['В проверенном объёме находок нет.', '']
    lines += ['## Ограничения', '', *['- ' + x for x in r['limitations']], '',
              'Отчёт не подтверждает готовность к выпуску. Невыполненные тесты не считаются прошедшими.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args = parser.parse_args()
    try:
        r = normalize(json.loads(args.report.read_text()))
        run = args.report.parent
        import importlib.util
        spec = importlib.util.spec_from_file_location('probe_runner', Path(__file__).with_name('runner.py'))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        inputs = json.loads((run / 'inputs.json').read_text())
        if r['run_id'] != inputs['run_id']:
            raise ValueError('report run_id differs from inputs')
        fresh = runner.freshness(run)
        if fresh['status'] != 'fresh':
            r['checks']['freshness'] = {'status': 'failed', 'reason': '; '.join(fresh['reasons'])}
        reverse_hashes = json.loads((run / 'reverse-hashes.json').read_text())
        if not reverse_hashes or runner.tree(run / 'reverse-change') != reverse_hashes:
            r['checks']['reverse'] = {'status': 'failed', 'reason': 'reverse missing or modified after reconstruction'}
        isolation = json.loads((run / 'isolation.json').read_text())
        fields = ['fresh_context', 'original_change_denied', 'host_escape_denied', 'network_denied']
        if (isolation.get('enforced_by') != 'runtime' or not isolation.get('mechanism')
            or not isolation.get('probe_log') or not all(isolation.get(k) is True for k in fields)):
            r['checks']['isolation'] = {'status': 'failed', 'reason': 'runtime evidence missing'}
        r = normalize(r)
    except (ValueError, OSError) as error:
        parser.exit(2, f'Invalid report: {error}\n')
    args.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    args.report.with_suffix('.md').write_text(markdown(r))
    print(r['verdict'])

if __name__=='__main__':
    main()
