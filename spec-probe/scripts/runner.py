#!/usr/bin/env python3
"""Host-side snapshots and full-cycle dispatcher. Python 3, standard library."""
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SUPPORT = Path(__file__).resolve().parents[1]
BLOCKED = {'.git', '.gigacode', '.spec-probe', '.openspec-store'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def hashes(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('Symlink forbidden: ' + str(p))
        if p.is_file():
            result[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def copy_tree(src, dst):
    hashes(src)  # Reject symlinks, including directory symlinks.
    shutil.copytree(src, dst)


def project_root(start):
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / '.gigacode/spec-probe').is_dir() or (p / '.spec-probe.json').is_file():
            return p
    return start


def discover(root, change, config):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', change):
        raise ValueError('Expected change ID, not a path')
    if config.get('plan_root'):
        plans = [(root / config['plan_root']).resolve()]
    else:
        plans = [root] + sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.'))
    matches = [p for p in plans if (p / 'openspec/changes' / change).is_dir()]
    if len(matches) != 1:
        raise ValueError('Cannot select unique change. Coordinator must resolve OpenSpec store and save plan_root; candidates: ' + str(matches))
    services = config.get('services')
    if services is None:
        src = root / 'src'
        services = {p.name: str(p.relative_to(root)) for p in sorted(src.iterdir()) if (p / '.git').exists()} if src.is_dir() else {}
        if not services and (root / '.git').exists() and root != matches[0]:
            services = {root.name: '.'}
        if not services and (root / '.git').exists() and not src.is_dir():
            services = {root.name: '.'}
    if not services:
        raise ValueError('No code repositories found; coordinator must set services')
    for name in services:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid service name')
    return matches[0], {k: (root / v).resolve() for k, v in services.items()}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def snapshot(repo, dest):
    # Include current tracked content, including staged/unstaged edits; record hashes.
    dest.mkdir(parents=True)
    for raw in git(repo, 'ls-files', '-z').split(b'\0'):
        if not raw:
            continue
        rel = Path(raw.decode())
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe tracked path')
        if any(x in BLOCKED for x in rel.parts) or 'openspec' in rel.parts:
            continue
        src = repo / rel
        if src.is_symlink() or any(p.is_symlink() for p in src.parents if p != repo.parent):
            raise ValueError('Symlink forbidden: ' + str(src))
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    untracked = git(repo, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    return {'base_sha': git(repo, 'rev-parse', 'HEAD').decode().strip(), 'files': hashes(dest),
            'excluded_untracked': [x for x in untracked if x], 'snapshot': 'current tracked working tree'}


def prepare(root, change, config):
    plan, services = discover(root, change, config)
    run = root / '.spec-probe' / uuid.uuid4().hex
    run.mkdir(parents=True)
    copy_tree(plan / 'openspec' / 'changes' / change, run / 'private/change')
    if (plan / 'openspec/specs').is_dir():
        copy_tree(plan / 'openspec/specs', run / 'baseline-specs')
    else:
        (run / 'baseline-specs').mkdir()
    metadata = {name: snapshot(repo, run / 'base' / name) for name, repo in services.items()}
    (run / 'sources').mkdir()
    sources = []
    for i, source in enumerate(config.get('sources', [])):
        src = (root / source['path']).resolve()
        if src.is_symlink() or not src.is_file():
            raise ValueError('Source must be a regular local export')
        dst = run / 'sources' / ('source-%d.txt' % i)
        shutil.copyfile(src, dst)
        sources.append({**source, 'snapshot': dst.name, 'sha256': hashlib.sha256(dst.read_bytes()).hexdigest()})
    manifest = {'run_id': run.name, 'change_id': change, 'plan_root': str(plan),
                'services': metadata, 'sources': sources, 'config': config,
                'input_hashes': {d: hashes(run / d) for d in ['base', 'private', 'sources', 'baseline-specs']},
                'status': 'prepared', 'completed': []}
    write(run / 'manifest.json', manifest)
    return run


def dispatch(command, stage, input_dir, output_dir, request):
    output_dir.mkdir(parents=True, exist_ok=True)
    req = output_dir.parent / (output_dir.name + '-request.json')
    write(req, request)
    subprocess.run([*command, '--stage', stage, '--input', str(input_dir), '--output', str(output_dir), '--request', str(req)], check=True)


def execute(run, command):
    if not command or not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError('Configure adapter as an argv array; see ADAPTER.md')
    manifest = json.loads((run / 'manifest.json').read_text())
    if manifest['status'] != 'prepared':
        raise ValueError('Run already started; create a fresh run to avoid mixed results')
    def save(status):
        manifest['status'] = status
        write(run / 'manifest.json', manifest)
    def check_inputs():
        for d, expected in manifest['input_hashes'].items():
            if hashes(run / d) != expected:
                raise ValueError('Input changed during run: ' + d)
    try:
        check_inputs()
        save('running')
        for index in range(1, 4):
            impl = 'impl-' + str(index)
            inp = run / 'generation-inputs' / impl
            copy_tree(run / 'base', inp / 'code')
            copy_tree(run / 'private/change', inp / 'change')
            copy_tree(run / 'sources', inp / 'sources')
            copy_tree(run / 'baseline-specs', inp / 'baseline-specs')
            output = run / 'implementations' / impl
            dispatch(command, 'generate', inp, output, {'impl': impl, 'fresh_context': True,
                     'instruction': 'Implement change independently. Write full code tree to output/code. Do not access other trials.'})
            if not (output / 'code').is_dir():
                raise ValueError('Generation did not produce code')
            hashes(output / 'code')
            manifest['completed'].append('generate/' + impl)
            save('running')
        for index in range(1, 4):
            impl = 'impl-' + str(index)
            package = run / 'packages' / impl
            copy_tree(run / 'implementations' / impl / 'code', package / 'code')
            copy_tree(run / 'base', package / 'base')
            copy_tree(run / 'sources', package / 'sources')
            copy_tree(run / 'baseline-specs', package / 'baseline-specs')
            before, after = hashes(package / 'base'), hashes(package / 'code')
            changes = []
            for name in sorted(before.keys() | after.keys()):
                if before.get(name) == after.get(name):
                    continue
                a, b = package / 'base' / name, package / 'code' / name
                changes.extend(difflib.unified_diff(a.read_text(errors='replace').splitlines(True) if a.exists() else [],
                    b.read_text(errors='replace').splitlines(True) if b.exists() else [], fromfile='base/' + name, tofile='code/' + name))
            (package / 'implementation.diff').write_text(''.join(changes))
            # Audit is outside reverse. It may read change to detect copied passages/paraphrases.
            audit_out = run / 'audits' / impl
            dispatch(command, 'audit', package, audit_out, {'change_path': str(run / 'private/change'),
                'instruction': 'Remove change copies and paraphrases from package, including sources, code comments, tests and rules. Preserve external task requirements; document redactions. Review baseline specs. Never send this request or audit output to reverse.'})
            audit = json.loads((audit_out / 'audit.json').read_text())
            if audit.get('approved') is not True or audit.get('package_hashes') != hashes(package):
                raise ValueError('Package audit missing or stale')
            frozen = hashes(package)
            output = run / 'reverse' / impl
            dispatch(command, 'reverse', package, output, {'impl': impl, 'fresh_context': True,
                'network': False, 'allowed_inputs': 'package only', 'instruction':
                'Reconstruct observed behavior. Also use sources (Jira/Confluence/other task descriptions). Separate source requirements from code evidence. No original change, history, sibling trials, parent context or host filesystem.'})
            isolation = json.loads((output / 'isolation.json').read_text())
            required = ['host_files_denied', 'change_denied', 'siblings_denied', 'network_denied', 'fresh_context']
            if isolation.get('enforced_by') != 'launcher' or not isolation.get('mechanism') or not isolation.get('probe_log') or not all(isolation.get(k) is True for k in required):
                raise ValueError('Launcher isolation evidence missing; reverse not accepted')
            if hashes(package) != frozen:
                raise ValueError('Reverse modified package')
            artifacts = hashes(output)
            if not any(x not in {'isolation.json', 'meta.json'} for x in artifacts):
                raise ValueError('Reverse artifacts missing')
            write(output / 'meta.json', {'impl': impl, 'package_hashes': frozen, 'artifact_hashes': artifacts,
                  'services': manifest['services'], 'isolation': 'verified', 'sources': hashes(package / 'sources')})
            manifest['completed'].append('reverse/' + impl)
            save('running')
        check_inputs()
        dispatch(command, 'compare', run, run / 'result', {'contract': str(SUPPORT / 'spec-review-agent.md'),
            'schema': str(SUPPORT / 'report.schema.json'), 'instruction':
            'Compare original change, external sources and all three reverse artifacts. Follow contract, validate freshness, run applicable checks. Write report.json. Missing checks must remain unknown.'})
        subprocess.run([sys.executable, str(SUPPORT / 'scripts/report.py'), str(run / 'result/report.json')], check=True)
        save('complete')
    except Exception as exc:
        manifest['error'] = str(exc)
        save('failed')
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('change')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = project_root(args.project)
    cfg = root / '.spec-probe.json'
    config = json.loads(cfg.read_text()) if cfg.exists() else {}
    if not args.prepare_only and not config.get('adapter'):
        raise ValueError('Execution adapter is not configured; coordinator must perform one-time setup described in ADAPTER.md')
    run = prepare(root, args.change, config)
    print(run, flush=True)
    if not args.prepare_only:
        execute(run, config['adapter'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('spec-probe: ' + str(exc), file=sys.stderr)
        sys.exit(1)
