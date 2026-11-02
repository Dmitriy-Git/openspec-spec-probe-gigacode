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
    findings = r['findings']
    if len({f['id'] for f in findings}) != len(findings):
        raise ValueError('duplicate finding id')
    for f in findings:
        cause, action = f['cause'], f['action']
        actions = {'spec_gap':{'rework','decision'},'spec_conflict':{'rework','decision'},
                   'implementation_defect':{'fix_implementation'},'allowed_variation':{'none'},
                   'environment_difference':{'investigate'},'insufficient_evidence':{'investigate'}}
        if action not in actions[cause]:
            raise ValueError(f'{f["id"]}: action contradicts cause')
        if f['patches'] and cause not in ('spec_gap','spec_conflict'):
            raise ValueError(f'{f["id"]}: code defects/unknowns cannot patch specifications')
        if f['observation'] == 'ac_gap':
            kinds = {e['kind'] for e in f['evidence']}
            if not {'requirement','artifact'} <= kinds:
                raise ValueError(f'{f["id"]}: ac_gap needs AC and reviewed artifacts')
            if cause not in ('spec_gap','insufficient_evidence'):
                raise ValueError(f'{f["id"]}: ac_gap cause contradicts missing specification coverage')
        mi = f['missing_input']
        if mi and mi['source']=='generation' and not any(e['kind']=='manifest' for e in mi['evidence']):
            raise ValueError(f'{f["id"]}: generation attribution needs input manifest evidence')
        for p in f['patches']:
            target = Path(p['target'])
            if target.is_absolute() or '..' in target.parts or '\\' in p['target'] or p['target']=='.':
                raise ValueError('unsafe patch path')
            digest = p['preimage']
            if p['operation']=='create':
                if digest!='absent' or p['old_text']:
                    raise ValueError('create requires absent preimage and empty old_text')
            elif len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('preimage must be SHA-256')
            if p['operation']=='replace' and not p['old_text']:
                raise ValueError('replace requires old_text')
            if p['operation']=='delete' and p['new_text']:
                raise ValueError('delete requires empty new_text')
    incomplete = r['isolation']!='verified' or any(x['blocking'] for x in r['limitations'])
    for name, check in r['checks'].items():
        if check['status']!='passed' and not (name=='behavior' and check['status']=='not_applicable'):
            incomplete = True
    uncertain = any(f['severity']!='low' and
                    (f['confidence']=='low' or f['cause'] in ('insufficient_evidence','environment_difference'))
                    for f in findings)
    spec = [f for f in findings if f['cause'] in ('spec_gap','spec_conflict')]
    rework = any(f['cause']=='spec_conflict' or f['observation']=='ac_gap' or
                 (f['action']=='rework' and f['severity']!='low') for f in spec)
    decisions = any(f['action']=='decision' and f['severity']!='low' for f in spec)
    quality = 'needs_rework' if rework else 'needs_decisions' if decisions else 'no_material_findings'
    r['verdict'] = 'inconclusive' if incomplete or uncertain else quality if quality!='no_material_findings' else 'spec_ready'
    r['summary'] = {'quality':quality,'analysis_complete':not (incomplete or uncertain),
                    'implementation_status':'defects_found' if any(f['cause']=='implementation_defect' for f in findings) else 'no_defects_observed',
                    'total':len(findings),'by_cause':dict(Counter(f['cause'] for f in findings)),
                    'by_observation':dict(Counter(f['observation'] for f in findings)),
                    'by_severity':dict(Counter(f['severity'] for f in findings))}
    return r

def markdown(r):
    lines = [f'# Spec probe: {r["run_id"]}', '', f'Verdict: **{r["verdict"]}**', '',
             f'Scope: {r["scope"]}', '', '## Итоги', '',
             '```json', json.dumps(r['summary'],ensure_ascii=False,indent=2), '```', '',
             '## Краткий список (до 20)', '']
    ranked = sorted(r['findings'],key=lambda f: {'high':0,'medium':1,'low':2}[f['severity']])
    lines += [f'- {f["id"]}: {f["severity"]}, {f["cause"]} — {f["rationale"]}' for f in ranked[:20]]
    lines += ['', '## Полные данные', '', 'Все находки, проверки, ограничения и доказательства:', '',
              '````json', json.dumps(r,ensure_ascii=False,indent=2), '````', '']
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args = parser.parse_args()
    try:
        r = normalize(json.loads(args.report.read_text()))
    except (ValueError, OSError) as error:
        parser.exit(2, f'Invalid report: {error}\n')
    args.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    args.report.with_suffix('.md').write_text(markdown(r))
    print(r['verdict'])

if __name__=='__main__':
    main()
