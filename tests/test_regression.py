import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'agents/openspec-spec-probe-fast/scripts'
def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
r = module('runner')
report = module('report')

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.change = self.root / 'store/openspec/changes/demo'
        self.change.mkdir(parents=True)
        (self.change / 'proposal.md').write_text('original')
        (self.root / 'store/openspec/specs').mkdir()
        self.repo = self.root / 'src/api'
        self.repo.mkdir(parents=True)
        self.cmd('init', '-q')
        (self.repo / 'code.py').write_text('base')
        self.cmd('add', '.')
        self.commit()
        self.base = self.cmd('rev-parse', 'HEAD').strip()
        (self.repo / 'code.py').write_text('implemented')
        self.cmd('add', '.')
        self.commit()
        self.services = {'api': {'path': str(self.repo), 'base': self.base}}

    def cmd(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args]).decode()

    def commit(self):
        self.cmd('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'test')

    def prepare(self):
        run = r.prepare(self.change, self.services, [])
        (run / 'observations.md').write_text('### OBS-01\ncode.py changed')
        r.freeze(run)
        return run

    def test_compact_summary_preserves_snapshot(self):
        import sys
        run = self.prepare()
        before = {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()}
        summary = r.input_summary(run)
        self.assertEqual(summary['services']['api']['base_sha'], self.base)
        self.assertEqual(summary['services']['api']['changed_paths'], ['code.py'])
        self.assertNotIn('files', summary['services']['api'])
        self.assertNotIn('change_hashes', summary)
        output = subprocess.check_output([sys.executable, '-B', str(SCRIPTS/'runner.py'), 'summary', str(run)])
        self.assertEqual(json.loads(output), summary)
        self.assertEqual(before, {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()})
        (self.repo/'code.py').write_text('changed after summary')
        self.assertNotEqual(r.freshness(run)['status'], 'fresh')

    def test_output_store_and_committed_diff(self):
        run = self.prepare()
        self.assertEqual(run.parent, self.change / 'reviews')
        data = json.loads((run / '_meta/inputs.json').read_text())
        self.assertEqual(data['services']['api']['changed_paths'], ['code.py'])
        self.assertFalse((run / 'implementations').exists())
        self.assertFalse((self.root / '.spec-probe').exists())
        self.assertEqual(r.freshness(run)['status'], 'fresh')

    def test_execution_metadata_and_tool_freshness(self):
        run = self.prepare()
        path = run / '_meta/inputs.json'
        data = json.loads(path.read_text())
        self.assertEqual(data['execution']['model'], 'unknown')
        self.assertLessEqual(data['execution']['prepare_started_at'], data['execution']['prepared_at'])
        self.assertLessEqual(data['execution']['prepared_at'], data['frozen_at'])
        self.assertEqual(len(data['tool_hashes']), 4)
        tool = self.root / 'tool.py'
        tool.write_text('original')
        data['tool_hashes'] = {str(tool): r.digest(tool)}
        path.write_text(json.dumps(data))
        tool.write_text('modified')
        self.assertIn('review tool changed: ' + str(tool), r.freshness(run)['reasons'])

    def test_explicit_model_metadata(self):
        run = r.prepare(self.change, self.services, [], model='test-model')
        data = json.loads((run / '_meta/inputs.json').read_text())
        self.assertEqual(data['execution']['model'], 'test-model')
        self.assertIn('not independently verified', data['execution']['model_source'])

    def test_working_edits_new_deleted_and_stale(self):
        (self.repo / 'staged.py').write_text('staged')
        self.cmd('add', '.')
        (self.repo / 'new.py').write_text('new')
        (self.repo / 'code.py').unlink()
        run = self.prepare()
        data = json.loads((run / '_meta/inputs.json').read_text())['services']['api']
        self.assertEqual(set(data['changed_paths']), {'staged.py', 'new.py', 'code.py'})
        self.assertNotIn('code.py', data['files'])
        (self.repo / 'new.py').write_text('changed during review')
        self.assertEqual(r.freshness(run)['status'], 'stale')

    def test_sources_and_change_freshness(self):
        src = self.root / 'task.md'
        src.write_text('Jira')
        run = r.prepare(self.change, self.services, [str(src)])
        (run / 'observations.md').write_text('Нет наблюдений')
        r.freeze(run)
        src.write_text('new requirements')
        (self.change / 'proposal.md').write_text('edited')
        self.assertEqual(len(r.freshness(run)['reasons']), 2)

    def test_rule_freshness(self):
        rule = self.repo / '.gigacode/rules.md'
        rule.parent.mkdir()
        rule.write_text('initial')
        run = r.prepare(self.change, self.services, [], rules=[rule])
        (run / 'observations.md').write_text('No observations')
        r.freeze(run)
        self.assertEqual(r.freshness(run)['status'], 'fresh')
        rule.write_text('changed')
        self.assertIn('rule changed: ' + str(rule), r.freshness(run)['reasons'])

    def test_missing_base_fails_before_run(self):
        self.services['api']['base'] = ''
        with self.assertRaisesRegex(ValueError, 'Missing base'):
            self.prepare()
        self.assertFalse((self.change / 'reviews').exists())

    def test_ambiguous_change(self):
        (self.root / 'other/openspec/changes/demo').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            r.resolve_change('demo', self.root)
        self.assertEqual(r.resolve_change(str(self.change), self.root), self.change)

    def test_symlink_rejected(self):
        (self.repo / 'link').symlink_to(self.change / 'proposal.md')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.prepare()

    def test_historical_master_specs(self):
        store = self.root / 'store'
        def git(*args):
            return subprocess.check_output(['git', '-C', str(store), *args]).decode()
        git('init', '-q')
        master = store / 'openspec/specs/main.md'
        master.write_text('baseline')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'baseline')
        base = git('rev-parse', 'HEAD').strip()
        master.write_text('synced version')
        run = r.prepare(self.change, self.services, [], base)
        (run / 'observations.md').write_text('Нет наблюдений')
        r.freeze(run)
        self.assertEqual(json.loads((run / '_meta/inputs.json').read_text())['baseline']['sha'], base)
        self.assertEqual(r.freshness(run)['status'], 'fresh')

    def test_report_cli_detects_stale_code(self):
        import sys
        run = self.prepare()
        data = json.loads((run / '_meta/inputs.json').read_text())
        value = {'schema_version':'8.0', 'run_id':data['run_id'], 'scope':'api', 'change_name':'demo', 'questions':[], 'reading_order': {'status':'unknown','basis':'No trace'}, 'coverage': [
                {**({'condition':'demo requirement'} if kind == 'scenario' else {}), 'kind': kind, 'ref': ref, 'status': 'confirmed', 'basis': 'code.py', 'finding_ids': []}
                for kind, ref in [('scenario', 'demo'), ('observation', 'OBS-01')]],
            'checks': {k: {'status':'passed','reason':'fixture', 'finding_ids':[]} for k in ['inputs','observations','requirements','comparison','freshness']},
            'tests': {'status':'not_run','reason':'static only'}, 'findings':[], 'limitations':[]}
        path = run / '_meta/report.json'
        path.write_text(json.dumps(value))
        subprocess.run([sys.executable, '-B', str(SCRIPTS / 'report.py'), str(path)], check=True, capture_output=True)
        self.assertTrue((run / 'report.md').is_file())
        self.assertTrue((run / 'review-details.md').is_file())
        published = json.loads((run / '_meta/publication.json').read_text())
        self.assertEqual(published['report_json_sha256'], r.digest(path))
        self.assertEqual(published['report_md_sha256'], r.digest(run / 'report.md'))
        self.assertEqual(published['review_details_sha256'], r.digest(run / 'review-details.md'))
        self.assertEqual(json.loads(path.read_text())['verdict'], 'no_material_findings')
        value['coverage'].pop()
        path.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'report.py'), str(path)], capture_output=True)
        self.assertIn(b'every frozen observation', result.stderr)
        value['coverage'].append({'kind':'observation', 'ref':'OBS-01', 'status':'unchecked',
                                  'basis':'missing dependency', 'finding_ids':[]})
        path.write_text(json.dumps(value))
        subprocess.run([sys.executable, '-B', str(SCRIPTS / 'report.py'), str(path)], check=True, capture_output=True)
        self.assertEqual(json.loads(path.read_text())['verdict'], 'partial_review')
        self.assertFalse((run / 'isolation.json').exists())
        self.assertFalse((run / '_meta/report.md').exists())
        (run / 'observations.md').write_text('modified after freeze')
        subprocess.run([sys.executable, '-B', str(SCRIPTS / 'report.py'), str(path)], check=True, capture_output=True)
        self.assertEqual(json.loads(path.read_text())['checks']['freshness']['status'], 'failed')
        (self.repo / 'code.py').write_text('later edit')
        subprocess.run([sys.executable, '-B', str(SCRIPTS / 'report.py'), str(path)], check=True, capture_output=True)
        self.assertEqual(json.loads(path.read_text())['verdict'], 'unable_to_review')

    def test_review_history_is_excluded_but_nested_spec_changes_are_inputs(self):
        first = self.prepare()
        (first / 'report.md').write_text('previous review')
        second = self.prepare()
        (second / 'report.md').write_text('another review')
        self.assertNotEqual(first, second)
        self.assertEqual(r.freshness(first)['status'], 'fresh')
        nested = self.change / 'specs/reviews'
        nested.mkdir(parents=True)
        (nested / 'spec.md').write_text('actual requirement')
        self.assertEqual(r.freshness(second)['status'], 'stale')

    def test_spec_decisions_for_lint_and_scope(self):
        sample = SCRIPTS.parents[2] / 'examples/report.json'
        data = json.loads(sample.read_text())
        finding = data['findings'][0]
        finding.update(summary='Некорректная опция ESLint', decision={
            'kind':'keep', 'basis':'AC требует проверки границы линтером'})
        rendered = report.markdown(report.normalize(data))
        self.assertIn('Сохранить требование', rendered)
        self.assertIn('Не запускались', rendered)
        patch = {'kind':'patch','target':'spec.md, граница','before':'весь src/api',
                 'after':'только три перечисленных файла','intent_source':'PR 12, решение автора', 'basis':'решение владельца в ревью'}
        finding['decision'] = patch
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            report.normalize(data)
        finding.update(cause='spec_conflict', incompatibility='Для четвёртого файла один пункт требует разрешить импорт, другой запрещает.', decision={'kind':'ask',
                       'question':'Разрешён ли четвёртый файл?', 'impact':'Область исключения'})
        self.assertIn('Открытых вопросов: 1', report.markdown(report.normalize(data)))
        finding['decision'] = patch
        self.assertIn('Готовых правок: 1', report.markdown(report.normalize(data)))
        del finding['incompatibility']
        finding.update(cause='spec_gap', original_change='Исключение отсутствует в проверенных сценариях')
        self.assertIn('review-details.md', report.markdown(report.normalize(data)))

    def test_order_is_separate_from_review_verdict(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        for status in ('violated', 'unknown', 'verified'):
            data['reading_order'] = {'status': status, 'basis': 'Synthetic trace'}
            normalized = report.normalize(data)
            self.assertEqual(normalized['verdict'], 'blocked_high_severity')
            self.assertNotIn('Порядок чтения', report.markdown(normalized))
            self.assertEqual('Исключить прогон' in report.details_markdown(normalized), status != 'verified')

    def test_question_count_matches_complete_registry(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        data['findings'][0].update(cause='spec_gap', decision={
            'kind': 'ask', 'question': 'Что делать при отказе?', 'impact': 'Уточнить ветку отказа'})
        data['questions'] = ['Что  делать при отказе? ', 'Нужен ли CI?', 'Нужен ли CI?']
        rendered = report.markdown(report.normalize(data))
        self.assertIn('Открытых вопросов: 2', rendered)
        registry = rendered.split('## Вопросы для решения')[1].split('## Существенные ограничения')[0]
        self.assertIn('1. Решение по находке [F-01](#finding-1).', registry)
        self.assertIn('2. Нужен ли CI?', registry)
        self.assertEqual(len(registry.strip().splitlines()), 2)

    def test_blank_evidence_and_duplicate_references_rejected(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        data['findings'][0]['evidence'] = ['  \n ']
        with self.assertRaises(ValueError):
            report.normalize(data)
        data['findings'][0]['evidence'] = ['code.py:42, static inspection']
        data['coverage'][0]['finding_ids'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate finding reference'):
            report.normalize(data)

    def test_compound_scenario_and_decision_evidence(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        location = data['coverage'][0]
        location.update(ref='UpdatesSTS', condition='Поместить в src/models/')
        extraction = dict(location, condition='Вынести из метода', status='confirmed',
                          basis='pages/UpdatesSTS/types/', finding_ids=[])
        data['coverage'].append(extraction)
        self.assertIn('Поместить в src/models/', report.details_markdown(report.normalize(data)))
        del extraction['condition']
        with self.assertRaisesRegex(ValueError, 'condition'):
            report.normalize(data)
        data['coverage'].pop()
        finding = data['findings'][0]
        finding.update(cause='spec_conflict', decision={'kind':'ask', 'question':'Таймаут?', 'impact':'Срок ожидания'})
        with self.assertRaisesRegex(ValueError, 'incompatibility'):
            report.normalize(data)
        finding.update(cause='spec_gap', decision={'kind':'patch', 'target':'spec.md', 'before':'все',
                                                   'after':'четыре', 'basis':'Наблюдение по коду'})
        with self.assertRaises(ValueError):
            report.normalize(data)
        finding['decision']['intent_source'] = 'Jira, Non-goals: миграция ограничена четырьмя'
        self.assertIn('Источник намерения', report.markdown(report.normalize(data)))

    def test_high_severity_blocks_even_partial_review(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        for status in ('passed', 'unknown', 'failed'):
            data['checks']['comparison']['status'] = status
            normalized = report.normalize(data)
            self.assertEqual(normalized['verdict'], 'blocked_high_severity')
            rendered = report.markdown(normalized)
            self.assertIn('Ревью не пройдено', rendered)
            self.assertIn('Блокирующие находки: F-01', rendered)
            self.assertIn('· 🔴 Высокая ·', rendered)
        data['findings'][0]['severity'] = 'medium'
        self.assertEqual(report.normalize(data)['verdict'], 'unable_to_review')
        data['checks']['comparison']['status'] = 'passed'
        for severity, label in [('medium', '🟡 Средняя'), ('low', '🟢 Низкая')]:
            data['findings'][0]['severity'] = severity
            normalized = report.normalize(data)
            self.assertEqual(normalized['verdict'], 'passed_with_remarks')
            self.assertIn('· ' + label + ' ·', report.markdown(normalized))

    def test_noncritical_findings_and_questions_are_normal(self):
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        data['findings'][0]['severity'] = 'medium'
        data['questions'] = ['Уточнить поведение?']
        self.assertEqual(report.normalize(data)['verdict'], 'passed_with_remarks')
        data['checks']['comparison']['status'] = 'unknown'
        self.assertEqual(report.normalize(data)['verdict'], 'partial_review')
        data['checks']['comparison']['status'] = 'passed'
        data['tests']['status'] = 'failed'
        self.assertEqual(report.normalize(data)['verdict'], 'needs_attention')

    def test_compact_report_sources_excerpts_and_deduplication(self):
        import copy
        data = json.loads((SCRIPTS.parents[2] / 'examples/report.json').read_text())
        finding = data['findings'][0]
        finding['code'] = 'Статически проверено: ветка отказа заканчивается исключением.'
        finding['sources'] = [{'section': 'code', 'path': 'src/api/config.ts',
                               'location': '37–39', 'language': 'typescript',
                               'excerpt': 'try {\n  await login();\n} catch (error) { throw error; }'}]
        finding['evidence'] = [finding['code'], 'src/api/config.ts:37–39',
                               'src/api/config.ts:37–39: проверен также вызывающий обработчик.']
        low = copy.deepcopy(finding)
        low.update(id='F-00', severity='low', summary='Low finding')
        data['findings'].insert(0, low)
        normalized = report.normalize(data)
        rendered = report.markdown(normalized)
        self.assertLess(rendered.index('### F-01'), rendered.index('### F-00'))
        first = rendered.split('### F-01')[1].split('### F-00')[0]
        self.assertEqual(first.count('📄 `src/api/config.ts:37–39`'), 1)
        self.assertIn('```typescript\ntry {', first)
        self.assertIn('проверен также вызывающий обработчик', first)
        self.assertLess(first.index('#### Решение'), first.index('#### Как реализовано'))
        for heading in ('## Область проверки', '## Список проверок', '## Покрытие', '## Порядок чтения'):
            self.assertNotIn(heading, rendered)
            self.assertIn(heading, report.details_markdown(normalized))
        self.assertIn('📄 `src/api/config.ts:12`', report.prose('Файл src/api/config.ts:12 проверен.'))
        literal = '```typescript\nconst path = "src/api/config.ts";\n```'
        self.assertEqual(report.prose(literal), literal)
        finding['sources'][0]['excerpt'] = '\n'.join(['line'] * 16)
        with self.assertRaisesRegex(ValueError, '15 lines'):
            report.normalize(data)

    def test_verdicts(self):
        data = {'schema_version': '8.0', 'run_id': 'test', 'scope': 'api', 'change_name':'demo', 'questions':[], 'reading_order': {'status':'unknown','basis':'No trace'}, 'coverage': [
                {**({'condition':'demo requirement'} if kind == 'scenario' else {}), 'kind': kind, 'ref': ref, 'status': 'confirmed', 'basis': 'code.py', 'finding_ids': []}
                for kind, ref in [('scenario', 'demo'), ('observation', 'OBS-01')]],
            'checks': {k: {'status':'passed', 'reason':'evidence', 'finding_ids':[]} for k in ['inputs','observations','requirements','comparison','freshness']},
            'tests': {'status':'not_run','reason':'static review only'}, 'findings': [], 'limitations': []}
        self.assertEqual(report.normalize(data)['verdict'], 'no_material_findings')
        data['checks']['requirements']['status'] = 'unknown'
        data['limitations'] = ['External source unavailable; local requirements reviewed']
        self.assertEqual(report.normalize(data)['verdict'], 'partial_review')
        data['checks']['requirements']['status'] = 'failed'
        self.assertEqual(report.normalize(data)['verdict'], 'unable_to_review')
        data['checks']['requirements']['status'] = 'passed'
        data['checks']['comparison']['status'] = 'failed'
        self.assertEqual(report.normalize(data)['verdict'], 'unable_to_review')
        data['checks']['comparison']['status'] = 'passed'
        data['tests']['status'] = 'failed'
        self.assertEqual(report.normalize(data)['verdict'], 'needs_attention')
        data['checks']['freshness']['status'] = 'failed'
        self.assertEqual(report.normalize(data)['verdict'], 'unable_to_review')
        data['checks']['freshness']['status'] = 'passed'
        data['findings'] = [{'id':'F1','severity':'medium','cause':'insufficient_evidence','summary':'unknown','problem':'Missing dependency behavior','observation_ids':['OBS-01'],'code':'api code.py: dependency not available','original_change':'proposal.md: required behavior','evidence':['search scope'], 'decision':{'kind':'verify','missing':'dependency behavior','check':'check dependency'}}]
        self.assertEqual(report.normalize(data)['verdict'], 'needs_attention')
        data['findings'].append(data['findings'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            report.normalize(data)

if __name__ == '__main__':
    unittest.main()
