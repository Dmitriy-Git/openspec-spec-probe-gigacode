import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runner', Path(__file__).resolve().parents[1] / 'scripts/runner.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'plans/openspec/changes/demo').mkdir(parents=True)
        (self.root / 'plans/openspec/changes/demo/spec.md').write_text('private change')
        repo = self.root / 'src/api'
        repo.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        (repo / 'api.py').write_text('BASE = 1\n')
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'base'], check=True)
        (self.root / 'task.txt').write_text('External task description')
        self.config = {'sources': [{'path': 'task.txt', 'url': 'jira:DEMO', 'version': '1'}]}

    def test_store_snapshots_and_sources(self):
        run = r.prepare(self.root, 'demo', self.config)
        self.assertEqual((run / 'sources/source-0.txt').read_text(), 'External task description')
        self.assertTrue((run / 'base/api/api.py').exists())
        self.assertFalse((run / 'base/api/.git').exists())
        self.assertFalse((run / 'base/api/openspec').exists())
        self.assertNotEqual(run, r.prepare(self.root, 'demo', self.config))

    def test_ambiguous_store_rejected(self):
        (self.root / 'other/openspec/changes/demo').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'unique'):
            r.discover(self.root, 'demo', {})

    def test_symlink_rejected(self):
        (self.root / 'plans/openspec/changes/demo/link').symlink_to(self.root / 'task.txt')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            r.prepare(self.root, 'demo', self.config)

    def test_changed_inputs_rejected(self):
        run = r.prepare(self.root, 'demo', self.config)
        (run / 'sources/source-0.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Input changed'):
            r.execute(run, ['unused'])
        self.assertEqual(json.loads((run / 'manifest.json').read_text())['status'], 'failed')

    def test_three_generations_then_fail_closed_reverse(self):
        run = r.prepare(self.root, 'demo', self.config)
        stages = []
        def fake(command, stage, inp, out, req):
            stages.append(stage)
            out.mkdir(parents=True, exist_ok=True)
            if stage == 'generate':
                r.copy_tree(inp / 'code', out / 'code')
            elif stage == 'audit':
                self.assertFalse((inp / 'change').exists())
                self.assertTrue((inp / 'sources/source-0.txt').exists())
                r.write(out / 'audit.json', {'approved': True, 'package_hashes': r.hashes(inp)})
            elif stage == 'reverse':
                r.write(out / 'isolation.json', {'enforced_by': 'model'})
        with patch.object(r, 'dispatch', fake):
            with self.assertRaisesRegex(ValueError, 'isolation evidence'):
                r.execute(run, ['unused'])
        self.assertEqual(stages, ['generate'] * 3 + ['audit', 'reverse'])
        self.assertEqual(json.loads((run / 'manifest.json').read_text())['status'], 'failed')

    def test_complete_dispatch_order(self):
        run = r.prepare(self.root, 'demo', self.config)
        stages = []
        def fake(command, stage, inp, out, req):
            stages.append(stage)
            out.mkdir(parents=True, exist_ok=True)
            if stage == 'generate':
                r.copy_tree(inp / 'code', out / 'code')
            elif stage == 'audit':
                r.write(out / 'audit.json', {'approved': True, 'package_hashes': r.hashes(inp)})
            elif stage == 'reverse':
                (out / 'spec.md').write_text('Observed base behavior; source requirements separately')
                r.write(out / 'isolation.json', dict(enforced_by='launcher', mechanism='TEST MOCK ONLY', probe_log='mock',
                    host_files_denied=True, change_denied=True, siblings_denied=True, network_denied=True, fresh_context=True))
        with patch.object(r, 'dispatch', fake), patch.object(r.subprocess, 'run') as validate:
            r.execute(run, ['unused'])
            validate.assert_called_once()
        self.assertEqual(stages, ['generate'] * 3 + ['audit', 'reverse'] * 3 + ['compare'])
        self.assertEqual(json.loads((run / 'manifest.json').read_text())['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
