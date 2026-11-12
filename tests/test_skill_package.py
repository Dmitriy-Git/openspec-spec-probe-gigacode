import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from test_regression import SCRIPTS
import test_regression

PACKAGE = Path(__file__).resolve().parents[1]

class SkillPackageTests(unittest.TestCase):
    def test_frontmatter_and_original_protocol_preserved(self):
        skill = SCRIPTS.parent / 'SKILL.md'
        text = skill.read_text()
        front, body = text.split('---', 2)[1:]
        fields = dict(line.split(': ', 1) for line in front.strip().splitlines())
        self.assertEqual(set(fields), {'name', 'description'})
        self.assertEqual(fields['name'], skill.parent.name)
        self.assertRegex(fields['name'], r'^[a-z0-9-]{1,64}$')
        self.assertTrue(fields['description'].strip())
        self.assertNotIn('\t', front)
        mapping = json.loads((PACKAGE / 'conversion-map.json').read_text())
        source = PACKAGE.parent / mapping['source']
        if not source.exists():
            self.skipTest('Original package unavailable; standalone distribution')
        original = source.read_text().split('---', 2)[2].lstrip()
        for before, after in mapping['body_replacements'].items():
            self.assertIn(before, original)
            original = original.replace(before, after)
        self.assertTrue(body.endswith(original))
        old = source.parent / 'openspec-spec-auditor'
        self.assertEqual((old / 'scripts/report.py').read_bytes(), (SCRIPTS / 'report.py').read_bytes())
        self.assertEqual((old / 'report.schema.json').read_bytes(), (SCRIPTS.parent / 'report.schema.json').read_bytes())
        runner = (old / 'scripts/runner.py').read_text()
        for before, after in mapping['runner_replacement'].items():
            runner = runner.replace(before, after)
        self.assertEqual(runner, (SCRIPTS / 'runner.py').read_text())

    def test_skill_change_invalidates_freshness(self):
        fixture = test_regression.ReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        installed = fixture.root / '.gigacode/skills/openspec-spec-auditor'
        shutil.copytree(SCRIPTS.parent, installed)
        spec = importlib.util.spec_from_file_location('installed_runner', installed / 'scripts/runner.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        run = runner.prepare(fixture.change, fixture.services, [])
        (run / 'observations.md').write_text('### OBS-01\ncode.py changed')
        runner.freeze(run)
        self.assertEqual(runner.freshness(run)['status'], 'fresh')
        skill = installed / 'SKILL.md'
        inputs = json.loads((run / '_meta/inputs.json').read_text())
        self.assertIn(str(skill), inputs['tool_hashes'])
        skill.write_text(skill.read_text() + '\nChanged protocol\n')
        self.assertNotEqual(runner.freshness(run)['status'], 'fresh')


    def test_source_must_be_file_before_run_is_created(self):
        fixture = test_regression.ReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for source in (fixture.root, fixture.root / 'missing.md'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, '--source must be an existing file'):
                    test_regression.r.prepare(fixture.change, fixture.services, [source])
                self.assertFalse((fixture.root / 'store/reviews').exists())
        source = fixture.root / 'external.md'
        source.write_text('External requirement')
        run = test_regression.r.prepare(fixture.change, fixture.services, [source])
        inputs = json.loads((run / '_meta/inputs.json').read_text())
        self.assertEqual(inputs['sources'], [{'path': str(source.resolve()),
                                             'sha256': test_regression.r.digest(source)}])
