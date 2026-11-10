"""Explicit report assembly, event timestamps and measured commands. Standard library."""
import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import subprocess
import time
import uuid


def event(run, kind, **fields):
    # One file per event allows independent commands without shared-file races.
    directory = Path(run) / '_meta' / 'timing'
    directory.mkdir(parents=True, exist_ok=True)
    record = {'at': datetime.now(timezone.utc).isoformat(), 'kind': kind, **fields}
    (directory / (uuid.uuid4().hex + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')


def assemble(manifest):
    manifest = Path(manifest).resolve()
    config = json.loads(manifest.read_text())
    groups = ('findings', 'coverage', 'evidence_registry')
    if set(config) != {'base', *groups}:
        raise ValueError('manifest requires base, findings, coverage, evidence_registry')
    used = set()
    def read(name):
        if not isinstance(name, str):
            raise ValueError('part path must be a string')
        path = (manifest.parent / name).resolve()
        if not path.is_relative_to(manifest.parent) or path == manifest or path in used:
            raise ValueError('duplicate or invalid part path')
        used.add(path)
        return json.loads(path.read_text())
    base = read(config['base'])
    if not isinstance(base, dict) or set(groups) & set(base):
        raise ValueError('base must be object without record arrays')
    base.pop('verdict', None)
    for group in groups:
        if not isinstance(config[group], list):
            raise ValueError('manifest groups must be path arrays')
        base[group] = []
        for name in config[group]:
            record = read(name)
            if not isinstance(record, dict):
                raise ValueError('each part must be one object')
            base[group].append(record)
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    mark = sub.add_parser('mark')
    mark.add_argument('run', type=Path)
    mark.add_argument('phase')
    mark.add_argument('boundary', choices=['start', 'end'])
    execute = sub.add_parser('exec')
    execute.add_argument('run', type=Path)
    execute.add_argument('--label', required=True)
    execute.add_argument('--basis', required=True)
    execute.add_argument('--cwd', type=Path, required=True)
    summary = sub.add_parser('summary')
    summary.add_argument('run', type=Path)
    argv = sys.argv[1:]
    separator = argv.index('--') if '--' in argv else len(argv)
    command = argv[separator+1:]
    args = parser.parse_args(argv[:separator])
    if args.action == 'mark':
        event(args.run, 'phase', phase=args.phase, boundary=args.boundary)
    elif args.action == 'summary':
        records = sorted((json.loads(p.read_text()) for p in (args.run/'_meta/timing').glob('*.json')), key=lambda x:x['at'])
        print(json.dumps({'scope':'Instrumented events only; not full agent time. Parallel durations overlap.', 'events':records}, ensure_ascii=False, indent=2))
    else:
        if command and command[0] == '--':
            command = command[1:]
        if not command:
            parser.error('command required after --')
        started = time.monotonic()
        invocation = uuid.uuid4().hex
        fields = dict(invocation=invocation, label=args.label, basis=args.basis, cwd=str(args.cwd.resolve()), command=command)
        event(args.run, 'command_start', **fields)
        try:
            result = subprocess.run(command, cwd=args.cwd, shell=False)
            code = result.returncode
        except OSError as error:
            event(args.run, 'command_end', **fields, duration_s=time.monotonic()-started, exit_code=None, error=str(error))
            raise SystemExit(127)
        except KeyboardInterrupt:
            event(args.run, 'command_interrupted', **fields, duration_s=time.monotonic()-started)
            raise SystemExit(130)
        event(args.run, 'command_end', **fields, duration_s=time.monotonic()-started, exit_code=code)
        raise SystemExit(code if code >= 0 else 128-code)


if __name__ == '__main__':
    main()
