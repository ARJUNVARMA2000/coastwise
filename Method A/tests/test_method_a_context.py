"""General ownership and negative controls for the retired-source repairs."""
import pytest
from test_four_table import NR, component, defect, fact
from test_method_a_grading import report_with_defects, source

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise import method_a_context as context


def fixture(text, heading='Span 4: Reinforced Concrete Deck'):
    pages = [{'page': 27, 'paragraphs': [source(27, 1, heading), source(27, 2, 'Condition State 3'), source(27, 3, text)]}]
    lookup = ft.paragraph_lookup(pages)
    d = defect('Concrete Damage', fact('spall', 'p27.3'), NR, fact(text, 'p27.3'))
    c = component(fact('Deck', 'p27.1'), '', NR, NR, fact(text, 'p27.3'), 'defects', [d])
    c.update({field: NR for field in ft.MATERIAL_FIELDS}, rating_scope='component')
    for item in c['defects']:
        item.update({field: NR for field in ft.MATERIAL_FIELDS}, severity_scale=NR, context_quotes=[])
    return c, lookup


def test_heading_material_stops_at_new_physical_member():
    c, lookup = fixture('Spall with exposed rebar.')
    assert context.bind_component(c, set(lookup), set(), lookup)['material']['v'] == 'Reinforced Concrete'
    c, lookup = fixture('A gap was observed.', 'Span 4: Movable Bearing')
    lookup['p27.0'] = source(27, 0, 'Span 3: Reinforced Concrete Deck') | {'page': 27}
    assert context.bind_component(c, set(lookup), set(), lookup)['material']['v'] == 'NR'


@pytest.mark.parametrize('text,expected', [
    ('The deck comprises a concrete slab with steel reinforcement.', 'concrete'),
    ('The deck is in good condition with local concrete spalls.', 'NR'),
    ('The proposed deck comprises a concrete slab.', 'NR'),
    ('Pier 8 deck comprises a concrete slab.', 'NR'),
    ('The piers consist of steel members supporting the deck.', 'NR'),
])
def test_construction_context_requires_same_member_and_current_construction(text, expected):
    c, lookup = fixture('Spalling was observed.', 'Deck')
    lookup['p4.1'] = source(4, 1, text) | {'page': 4}
    material = context.document_material_context(c, {'p4.1'}, lookup)
    assert (material[1] if material else 'NR') == expected


def test_glossary_material_does_not_leak_from_other_members():
    c, lookup = fixture('Deterioration was observed.', 'Stringers')
    c['name'] = fact('stringers', 'p27.1')
    lookup['p4.1'] = source(4, 1, 'Piles: refer to timber piles.\nx\nStringers: refer to steel members.') | {'page': 4}
    assert context.document_material_context(c, {'p4.1'}, lookup) == ('p4.1', 'steel')


@pytest.mark.parametrize('text,grade', [
    ('A spall 7 SF x 2 inches deep with exposed rebar.', 3),
    ('Some spalling along joint with mesh exposed.', None),
    ('No spall with exposed rebar was observed.', None),
    ('A possible spall with exposed rebar was noted.', None),
    ('Spalling was observed; rebar exposed elsewhere.', None),
])
def test_open_spall_equivalence_stays_in_affirmative_owned_clause(text, grade):
    d = defect('Concrete Damage', fact('spall', 'p9.1'), NR, fact('Concrete. ' + text, 'p9.1'))
    pages = [{'page': 1, 'paragraphs': [source(1, 1, 'Pile P6 rated Fair.')]},
             {'page': 9, 'paragraphs': [source(9, 1, 'Concrete. ' + text)]}]
    item, = dg.grading_inputs(report_with_defects(d, pages=pages))
    assert dg.local_assessment(item)[1]['grade'] == grade


def test_historical_ratings_stop_at_current_inspection_heading():
    lookup = ft.paragraph_lookup([
        {'page': 20, 'paragraphs': [source(20, 1, '7 PREVIOUS REPORTS'), source(20, 2, 'The 2012 report is reproduced here.')]},
        {'page': 21, 'paragraphs': [source(21, 1, 'Piles assigned a condition rating of 4, based on previous inspections.'),
                                  source(21, 2, '8 CURRENT INSPECTION'), source(21, 3, 'Piles attract a condition rating of 2.')]}])
    got = context.historical_conditions({'p21.1', 'p21.3'}, set(lookup), lookup, '2020')
    assert len(got) == 1 and got[0]['condition']['v'] == '4' and got[0]['source_year'] == '2012'
    assert 'previous inspections' in got[0]['source_scope']['v']


@pytest.mark.parametrize('prefix,recovered', [('', True), ('The proposed work: ', False), ('It is possible that ', False)])
def test_observed_level_recovery_preserves_literal_quote_and_conditional_remedy(prefix, recovered):
    text = prefix + 'The slab level is uneven and should be corrected if rehabilitated.'
    lookup = ft.paragraph_lookup([{'page': 42, 'paragraphs': [source(42, 1, text)]}])
    rows = context.recovered_level_observations([], set(lookup), lookup, 'S1')
    assert bool(rows) == recovered
    if rows:
        finding = rows[0]['defects'][0]['findings_text']['v']
        assert ft.verified_quote(finding, text) == finding and 'if' not in finding
        assert context.recovered_level_observations(rows, set(lookup), lookup, 'S1') == []


def test_count_is_affected_population_and_geometry_is_not_a_rating():
    c, lookup = fixture('Several deck slabs have deteriorated and need replacement.')
    c['name'] = fact('deck slabs', 'p27.3')
    d = c['defects'][0]
    d['severity'] = fact('shallow', 'p27.3')
    lookup['p8.1'] = source(8, 1, 'In total there are 36 deck slabs requiring replacement.') | {'page': 8}
    bound = context.bind_component(c, set(lookup), {'p8.1'}, lookup)
    assert bound['defects'][0]['quantity']['v'] == '36'
    assert bound['defects'][0]['severity']['v'] == 'NR'
    lookup['p8.1']['text'] = 'In total there are 36 deck slabs in the structure.'
    assert context.count_context(c, d, {'p8.1'}, lookup) is None


def test_published_snapshot_keeps_original_freeze_and_rejects_changed_input():
    from copy import deepcopy

    from coastwise import method_a_replays
    metadata, reports = method_a_replays.bundle()
    original = reports['2018']
    changed = deepcopy(original)
    changed['defects'][0]['grading_input_sha256'] = 'bad'
    with pytest.raises(ValueError, match='Invalid replay assessment'):
        method_a_replays.verify(changed, metadata['reports']['2018'], metadata['freeze'], archived_contract=True)
    with pytest.raises(ValueError, match='incompatible'):
        method_a_replays.verify(original, metadata['reports']['2018'], metadata['freeze'])
