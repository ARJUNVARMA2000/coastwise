"""Replay first-evaluation responses offline; never alter or dispatch a saved request."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from replay_brooklyn_fresh_holdouts import candidates, replay_candidate

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'docs/method-a/evaluations/2026-11-11/results'
LIVE = ROOT / 'output/coastwise/method-a-fresh-evaluation-2026-11-11/live'


def read(path):
    return json.loads(path.read_text())


def replay(output):
    if output.exists():
        raise ValueError('Use a new replay directory; first and subsequent results are immutable')
    originals = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in FIRST.rglob('*') if p.is_file()}
    originals.update({str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in LIVE.rglob('*') if p.is_file()})
    numeric = read(FIRST / 'numeric-rule-results.json')['checks']
    checks, reports, tables_by_source = {}, {}, {}
    for name in ('nysdot_106666c_2016', 'derby_jetty_2016', 'esperance_jetty_2016'):
        result, pages = ft.load_run(LIVE / name / 'extraction')
        tables = ft.assemble([(result, pages)], reviewed_overrides=False)
        lookup = ft.paragraph_lookup(pages)
        for c in tables['citations']:
            if c['verified_quote']:
                assert c['verified_quote'] in lookup[c['tag']]['text']
                assert (c['page'], c['paragraph']) == (lookup[c['tag']]['page'], lookup[c['tag']]['n'])
        inputs = {i['defect_id']: i for i in dg.grading_inputs(tables)}
        model = result['metrics']['model']
        saved = candidates(LIVE / name / 'grading/attempts', model, 999999)
        reused = 0
        for row in tables['defects']:
            item = inputs[row['defect_id']]
            local = dg.local_assessment(item)
            if local is None:
                assessment = replay_candidate(saved[row['defect_id']], item)
                local = ('insufficient_evidence', assessment)
                reused += 1
            basis, assessment = local
            dg.validate_assessment(assessment, item)
            row.update(dg.enforce_rubric_scope(item, dg.assessment_fields(
                assessment, basis, dg.grading_versions(result['metrics']['model']), item)))
        tables_by_source[name] = tables
        ft.write_dataset(output / name, tables)
        fields, verified = set(), set()
        for c in tables['citations']:
            key = (c['table'], c['row_id'], c['field'])
            fields.add(key)
            if c['verified_quote']:
                verified.add(key)
        reports[name] = {'source_sha256': result['input_sha256'], 'coverage': tables['manifest'][0]['coverage'],
                         'counts': {k: len(tables[k]) for k in ('component_observations', 'defects', 'measurements')},
                         'statuses': dict(Counter(d['grading_status'] for d in tables['defects'])),
                         'verified_fragments': sum(bool(c['verified_quote']) for c in tables['citations']),
                         'citation_records': len(tables['citations']), 'claimed_fields': len(fields),
                         'fields_with_verified_fragment': len(verified),
                         'reviewed_overrides': tables['manifest'][0]['reviewed_overrides']}
        reports[name]['saved_proposals_revalidated_not_cache_hits'] = reused
        assert reports[name]['reviewed_overrides'] is False
        assert not any(d['grading_status'] in ('failed', 'pending') for d in tables['defects'])
    for spec in numeric:
        name = 'nysdot_106666c_2016' if spec['source_target'].startswith('NY') else 'derby_jetty_2016'
        rows = {d['defect_id']: d for d in tables_by_source[name]['defects']}
        checks[spec['id']] = all(rows[k]['damage_grade'] == spec['expected_grade']
            and rows[k]['assessment_rule_id'] == spec['expected_rule_id'] for k in spec['accepted_defect_ids'])
    derby = tables_by_source['derby_jetty_2016']
    checks['DY-C01'] = all(o['material'] == 'concrete' and o['structural_role'] == 'running surface'
        for o in derby['component_observations']) and any('150mm' in c['verified_quote']
        and 'steel reinforcement' in c['verified_quote'] and 'does not form part of the structural frame' in c['verified_quote']
        for c in derby['citations'])
    es = tables_by_source['esperance_jetty_2016']
    irregular = [d for d in es['defects'] if d['defect_type'] == 'irregular']
    checks['discovery:ES-F02'] = len(irregular) == 1 and irregular[0]['damage_grade'] is None
    checks['attribution:ES-F02'] = len(irregular) == 1 and irregular[0]['temporal_status'] == 'current' and any(
        c['row_id'] == irregular[0]['defect_id'] and c['field'] == 'findings_text' and c['page'] == 9
        and 'deck level is quite irregular' in c['verified_quote'] for c in es['citations'])
    checks['measurement:ES-F01'] = any(d['defect_type'] == 'deteriorated' and d['quantity'] == '48' for d in es['defects'])
    inspection, = es['inspections']
    checks['ES-R01'] = inspection['rating_scale'] == 'NR' and all(
        s.get('temporal_status') == 'historical' and s.get('source_year') == '2015' for s in inspection['rating_scales'])
    history = inspection['scoped_conditions']
    for check, rating, tokens in [('ES-R02', '2-3', ['exception of bents 93, 94, and 95', 'southern']),
                                  ('ES-R03', '5-6', ['northern', '33-45 and 133']),
                                  ('ES-R04', '4', ['not been inspected', 'based on previous inspections']),
                                  ('ES-R05', '6-7', ['worst condition', '2015 Report'])]:
        checks[check] = any(h['condition'] == rating and h['temporal_status'] == 'historical'
            and h['source_year'] == '2015' and all(t in h['source_scope'] for t in tokens) for h in history)
    assert len(checks) == 16
    assert all(digest((ROOT / p).read_bytes()) == h for p, h in originals.items())
    summary = {'mode': 'saved-response assembly and local rule replay, not a model re-extraction',
               'paid_requests': 0, 'spend_usd': 0, 'reviewed_overrides': False,
               'first_evaluation_unchanged': True, 'first_artifact_sha256': originals,
               'regression_checks': checks, 'reports': reports,
               'limitations': ['Prompt changes are untested on a fresh model response.',
                   'Generic recovery is deliberately limited to explicit native statements.',
                   'Original metadata/field citation gaps remain visible; no claimed-field perfection.',
                   'Ambiguous Derby Pier 48 remains a component observation without a numeric defect grade.',
                   'No new compatible source crosswalk or raster scale was exercised.']}
    save_json(output / 'replay-summary.json', summary)
    print(json.dumps({'repair_checks_passed': sum(checks.values()), 'repair_checks_total': len(checks), 'reports': reports}, indent=2))
    if not all(checks.values()):
        raise ValueError('Retired-source repair gate failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    replay(parser.parse_args().output.resolve())
