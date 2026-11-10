import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_regression import SCRIPTS, module
report = module('report')
workflow = module('workflow')

class WorkflowTests(unittest.TestCase):
    def sample(self):
        data = json.loads((SCRIPTS.parents[2]/'examples/report.json').read_text())
        data['evidence_registry'] = [{'id':'E-01','source':'code.py','location':'1–5','fact':'Observed behavior','method':'static analysis'}]
        data['findings'][0]['evidence_ids'] = ['E-01']
        data['coverage'][0]['evidence_ids'] = ['E-01']
        return data

    def test_evidence_links_and_rejections(self):
        data=self.sample()
        normalized=report.normalize(data)
        self.assertIn('review-details.md#evidence-E-01', report.markdown(normalized))
        self.assertIn('id="evidence-E-01"', report.details_markdown(normalized))
        for links in (['E-99'], ['E-01','E-01']):
            bad=copy.deepcopy(data);bad['coverage'][0]['evidence_ids']=links
            with self.assertRaises(ValueError): report.normalize(bad)
        data['evidence_registry']*=2
        with self.assertRaises(ValueError): report.normalize(data)

    def test_parts_roundtrip_and_path_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=self.sample();base=copy.deepcopy(data)
            config={'base':'base.json'}
            for group in ('findings','coverage','evidence_registry'):
                config[group]=[]
                for i, record in enumerate(base.pop(group)):
                    name=f'{group}-{i}.json';config[group].append(name)
                    (root/name).write_text(json.dumps(record))
            (root/'base.json').write_text(json.dumps(base));manifest=root/'manifest.json'
            manifest.write_text(json.dumps(config))
            self.assertEqual(report.normalize(workflow.assemble(manifest)),report.normalize(data))
            config['coverage'].append(config['coverage'][0]);manifest.write_text(json.dumps(config))
            with self.assertRaises(ValueError): workflow.assemble(manifest)
            config['base']='../outside.json';manifest.write_text(json.dumps(config))
            with self.assertRaises(ValueError): workflow.assemble(manifest)

    def test_command_exit_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-B',str(SCRIPTS/'workflow.py'),'exec',directory,'--label','failure','--basis','test','--cwd',directory,'--',sys.executable,'-c','raise SystemExit(7)'])
            self.assertEqual(result.returncode,7)
            events=[json.loads(p.read_text()) for p in (Path(directory)/'_meta/timing').glob('*.json')]
            self.assertEqual(len(events),2)
            end=next(e for e in events if e['kind']=='command_end')
            self.assertEqual(end['exit_code'],7);self.assertGreaterEqual(end['duration_s'],0)

    def test_scope_exclusion_restricted(self):
        data=self.sample();data['coverage'][0]['in_scope']=False
        with self.assertRaises(ValueError): report.normalize(data)
