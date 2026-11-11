#!/usr/bin/env python3
"""Capture review inputs and check freshness. Never generates code or invokes agents."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

EXCLUDE = {'.git', '.spec-probe', '.gigacode'}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def digest(path):
    if path.is_symlink():
        raise ValueError('Symlink is not an accepted input: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path, skip_reviews=False):
    result = {}
    for child in sorted(path.iterdir()) if path.exists() else []:
        if skip_reviews and child.name == 'reviews':
            continue
        p = child
        if p.is_symlink():
            raise ValueError('Symlink is not an accepted input: ' + str(p))
        if p.is_dir():
            result.update({p.name + "/" + name: sha for name, sha in tree(p).items()})
        elif p.is_file():
            result[p.name] = digest(p)
    return result


def resolve_change(value, start):
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        change = candidate.resolve()
    else:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
            raise ValueError('Provide a change directory or change ID')
        matches = set()
        for parent in [start.resolve(), *start.resolve().parents]:
            local = parent / 'openspec/changes' / value
            if local.is_dir():
                matches.add(local.resolve())
            if (parent / '.gigacode').is_dir() or (parent / 'src').is_dir():
                for child in parent.iterdir():
                    p = child / 'openspec/changes' / value
                    if p.is_dir():
                        matches.add(p.resolve())
                break
        if len(matches) != 1:
            raise ValueError('Change is missing or ambiguous; coordinator must resolve OpenSpec store and pass exact path')
        change = matches.pop()
    if change.parent.name != 'changes' or change.parent.parent.name != 'openspec':
        raise ValueError('Expected <planning-root>/openspec/changes/<change-id>')
    return change


def working_files(repo):
    names = git(repo, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0')
    result = {}
    for name in sorted(set(names) - {''}):
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe git path')
        if any(x in EXCLUDE for x in rel.parts) or 'openspec' in rel.parts:
            continue
        p = repo / rel
        if any(parent.is_symlink() for parent in [p, *p.parents]):
            raise ValueError('Symlink input: ' + name)
        if p.is_dir():
            raise ValueError('Submodule requires explicit service entry: ' + name)
        if p.is_file():
            result[name] = digest(p)
    return result


def capture_service(repo, base):
    repo = repo.resolve()
    top = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != repo:
        raise ValueError('Service path must be repository root')
    if not base:
        raise ValueError('Missing base for ' + str(repo) + '; coordinator must select feature starting commit, not assume HEAD')
    if base.startswith('-'):
        raise ValueError('Invalid base revision')
    sha = git(repo, 'rev-parse', '--verify', base + '^{commit}').decode().strip()
    changed = git(repo, 'diff', '--name-only', '-z', sha, '--').decode().split('\0')
    untracked = git(repo, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    return {'path': str(repo), 'base_sha': sha,
            'head_sha': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'files': working_files(repo), 'changed_paths': sorted(set(changed + untracked) - {''})}


def prepare(change, services, sources, baseline_ref=None, rules=(), model='unknown'):
    started_at = datetime.now(timezone.utc).isoformat()
    store = change.parent.parent.parent
    specs = store / 'openspec/specs'
    baseline = {'path': str(specs), 'mode': 'current', 'files': tree(specs),
                'confirmation': 'Coordinator must confirm these specs predate this change'}
    if baseline_ref:
        if baseline_ref.startswith('-'):
            raise ValueError('Invalid baseline revision')
        sha = git(store, 'rev-parse', '--verify', baseline_ref + '^{commit}').decode().strip()
        prefix = git(store, 'rev-parse', '--show-prefix').decode().strip() + 'openspec/specs'
        listing = git(store, 'ls-tree', '-r', sha, '--', prefix)
        if not listing:
            raise ValueError('No baseline specs at selected revision')
        baseline = {'path': str(specs), 'mode': 'git', 'sha': sha,
                    'tree_digest': hashlib.sha256(listing).hexdigest()}
    data = {'schema_version': '8.0', 'rules': [{'path': str(Path(p).absolute()), 'sha256': digest(Path(p))} for p in rules], 'review_mode': 'sequential_single_agent', 'change_path': str(change), 'change_hashes': tree(change, skip_reviews=True),
            'baseline': baseline, 'services': {name: capture_service(Path(s['path']), s['base']) for name, s in services.items()},
            'sources': [{'path': str(Path(s).resolve()), 'sha256': digest(Path(s))} for s in sources],
            'status': 'prepared', 'scope_note': 'Unignored untracked files included; ignored files excluded. No file contents copied.'}
    if not data['services']:
        raise ValueError('At least one code repository is required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    run = change / 'reviews' / run_id
    # Do not follow a redirected output directory.
    if any(p.is_symlink() for p in [run, *run.parents]):
        raise ValueError('Review output path contains a symlink')
    run.mkdir(parents=True)
    (run / '_meta').mkdir()
    data['run_id'] = run_id
    data['execution'] = {
        'prepare_started_at': started_at,
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'model': model.strip() or 'unknown',
        'model_source': 'caller supplied; not independently verified' if model.strip() and model.strip() != 'unknown' else 'unknown',
        'timing_scope': 'prepare to report publication; excludes agent work before prepare',
        'python_version': sys.version.split()[0],
    }
    package = Path(__file__).resolve().parents[1]
    tool_files = [Path(__file__).resolve(), Path(__file__).with_name('report.py'),
                  package / 'report.schema.json', package.parent / 'openspec-spec-auditor.md']
    data['tool_hashes'] = {str(p): digest(p) for p in tool_files}
    (run / '_meta/inputs.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return run


def freeze(run):
    path = run / '_meta/inputs.json'
    data = json.loads(path.read_text())
    if 'observations_sha256' in data:
        raise ValueError('Observations already frozen')
    notes = run / 'observations.md'
    if not notes.read_text().strip():
        raise ValueError('Empty observations')
    data['observations_sha256'] = digest(notes)
    data['frozen_at'] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def freshness(run):
    data = json.loads((run / '_meta/inputs.json').read_text())
    errors = []
    if tree(Path(data['change_path']), skip_reviews=True) != data['change_hashes']:
        errors.append('original change changed')
    notes = run / 'observations.md'
    if not notes.is_file() or digest(notes) != data.get('observations_sha256'):
        errors.append('observations missing, unfrozen or modified')
    baseline = data['baseline']
    if baseline['mode'] == 'current' and tree(Path(baseline['path'])) != baseline['files']:
        errors.append('master specs changed')
    for name, service in data['services'].items():
        repo = Path(service['path'])
        if working_files(repo) != service['files'] or git(repo, 'rev-parse', 'HEAD').decode().strip() != service['head_sha']:
            errors.append('code changed: ' + name)
    for rule in data.get('rules', []):
        path = Path(rule['path'])
        if path.is_symlink() or not path.is_file() or digest(path) != rule['sha256']:
            errors.append('rule changed: ' + str(path))
    for source in data['sources']:
        p = Path(source['path'])
        if not p.is_file() or digest(p) != source['sha256']:
            errors.append('source changed: ' + str(p))
    for name, sha in data.get('tool_hashes', {}).items():
        p = Path(name)
        if p.is_symlink() or not p.is_file() or digest(p) != sha:
            errors.append('review tool changed: ' + name)
    result = {'status': 'stale' if errors else 'fresh', 'reasons': errors,
              'checked_at': datetime.now(timezone.utc).isoformat()}
    (run / '_meta/freshness.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def assignments(values):
    result = {}
    for value in values:
        name, sep, val = value.partition('=')
        if not sep or not name or not val or name in result:
            raise ValueError('Expected unique NAME=VALUE')
        result[name] = val
    return result


def input_summary(run):
    """Metadata only; no requirement contents or per-file hash maps."""
    run = run.resolve()
    data = json.loads((run / '_meta/inputs.json').read_text())
    baseline = data['baseline']
    return {
        'run_path': str(run), 'run_id': data['run_id'],
        'change_path': data['change_path'], 'status': data['status'],
        'baseline': {k: baseline[k] for k in ('path', 'mode', 'sha', 'confirmation') if k in baseline},
        'services': {name: {**{k: service[k] for k in ('path', 'base_sha', 'head_sha')},
                           'changed_paths': [path for path in service['changed_paths']
                                             if not any(part in EXCLUDE or part in ('openspec', 'reviews') for part in Path(path).parts)],
                           'tracked_file_count': len(service['files'])}
                     for name, service in data['services'].items()},
        'rule_paths': [item['path'] for item in data.get('rules', [])],
        'source_paths': [item['path'] for item in data.get('sources', [])],
        'note': 'Saved snapshot metadata, not a freshness check. Use check to verify freshness. Changed paths are code candidates, not full analysis scope.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('change')
    p.add_argument('--project', type=Path, default=Path.cwd())
    p.add_argument('--service', action='append', default=[])
    p.add_argument('--base', action='append', default=[])
    p.add_argument('--source', action='append', default=[])
    p.add_argument('--rule', action='append', default=[])
    p.add_argument('--baseline-ref')
    p.add_argument('--model', default='unknown')
    p = commands.add_parser('check')
    p.add_argument('run', type=Path)
    p = commands.add_parser('freeze')
    p.add_argument('run', type=Path)
    p = commands.add_parser('summary')
    p.add_argument('run', type=Path)
    args = parser.parse_args()
    if args.command == 'summary':
        print(json.dumps(input_summary(args.run), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'freeze':
        freeze(args.run.resolve())
        return 0
    if args.command == 'check':
        result = freshness(args.run.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'fresh' else 2
    paths, bases = assignments(args.service), assignments(args.base)
    if paths.keys() != bases.keys():
        raise ValueError('Each service requires one base; coordinator resolves these, user need not supply CLI variables')
    services = {name: {'path': str((args.project / path).resolve()), 'base': bases[name]} for name, path in paths.items()}
    print(prepare(resolve_change(args.change, args.project), services, args.source, args.baseline_ref, args.rule, args.model))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('spec-auditor: ' + str(error), file=sys.stderr)
        sys.exit(1)
