import copy
import json
import unittest
from test_regression import SCRIPTS, report

class CompactTests(unittest.TestCase):
    def setUp(self):
        self.original=json.loads((SCRIPTS.parents[2]/'examples/report.json').read_text())
        self.compact=copy.deepcopy(self.original)
        self.compact['reference_map']=[]
        refs={}
        for row in self.compact['coverage']:
            if row['kind']=='observation': continue
            ref=row.pop('ref')
            if ref not in refs:
                refs[ref]=f'R-{len(refs)+1:02}'
                self.compact['reference_map'].append({'id':refs[ref],'ref':ref})
            row['ref_id']=refs[ref]

    def test_identical_render_and_compact_normalization(self):
        old=report.normalize(self.original);new=report.normalize(self.compact)
        self.assertEqual(report.markdown(old),report.markdown(new))
        self.assertEqual(report.details_markdown(old),report.details_markdown(new))
        self.assertEqual(new['coverage'],self.compact['coverage'])
        self.assertEqual(report.normalize(new),new)

    def test_invalid_reference_and_ambiguous_fields(self):
        row=next(row for row in self.compact['coverage'] if 'ref_id' in row)
        row['ref_id']='R-9999'
        with self.assertRaisesRegex(ValueError,'unknown'): report.normalize(self.compact)
        row['ref']='duplicate'
        with self.assertRaisesRegex(ValueError,'exactly one'): report.normalize(self.compact)

    def test_duplicate_map_and_resolved_coverage(self):
        bad=copy.deepcopy(self.compact);bad['reference_map'].append(bad['reference_map'][0])
        with self.assertRaisesRegex(ValueError,'duplicate reference'): report.normalize(bad)
        bad=copy.deepcopy(self.compact)
        row=copy.deepcopy(next(row for row in bad['coverage'] if 'ref_id' in row))
        row['ref']=report.coverage_ref(bad,row);del row['ref_id'];bad['coverage'].append(row)
        with self.assertRaisesRegex(ValueError,'duplicate coverage'): report.normalize(bad)

    def test_observation_ids_stay_direct(self):
        row=next(row for row in self.compact['coverage'] if row['kind']=='observation')
        del row['ref'];row['ref_id']=self.compact['reference_map'][0]['id']
        with self.assertRaisesRegex(ValueError,'observation'):report.normalize(self.compact)
