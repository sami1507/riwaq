"""Synthetic, deterministic data acceptance; no Office, network or model mocks."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.skill_artifacts import build_artifact
from backend.skill_data import DataRequest, execute_data, profile, statistics, recalculate, S, C, A, NS


ORDERS = {'columns': ['order_id', 'customer_id', 'amount'], 'rows': [['O1', 'C1', 10], ['O2', 'C1', 0], ['O3', 'C2', 20], ['O4', 'C9', None]]}
CUSTOMERS = {'columns': ['customer_id', 'region'], 'rows': [['C1', 'north'], ['C2', 'south']]}


@pytest.fixture
def template():
    return build_artifact('xlsx', {'title': 'Data', 'columns': [{'name': 'Value'}], 'rows': [['']]}).content


def request(operation, table=ORDERS, **kw):
    return {'operation': operation, 'table': deepcopy(table), **kw}


def rewrite(content, name, transform):
    with ZipFile(BytesIO(content)) as zipped:
        parts = {p: zipped.read(p) for p in zipped.namelist()}
    parts[name] = transform(parts[name])
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as zipped:
        for path, raw in parts.items(): zipped.writestr(path, raw)
    return output.getvalue()


def test_orders_reference_statistics_and_profile():
    result = execute_data(request('stats', columns=['amount'], record_unit='one order'))
    observed = result.summary['statistics']['amount']
    assert {key: observed[key] for key in ('count', 'numeric_count', 'missing', 'sum', 'mean', 'median')} == {'count': 4, 'numeric_count': 3, 'missing': 1, 'sum': '30', 'mean': '10', 'median': '10'}
    assert observed['stddev'] == '10'
    assert result.table['rows'] == ORDERS['rows']
    assert result.summary['profile']['scope'].startswith('all bounded rows')


def test_join_reference_and_duplicate_preflight():
    args = request('join', right=CUSTOMERS, join={'left_keys': ['customer_id'], 'right_keys': ['customer_id'], 'how': 'left', 'cardinality': 'm:1'})
    result = execute_data(args)
    assert len(result.table['rows']) == 4
    assert result.summary['join']['unmatched_left'] == 1
    assert result.summary['join']['lineage_row_indices'][-1] == [3, None]
    duplicate = deepcopy(CUSTOMERS); duplicate['rows'].append(['C1', 'alternate'])
    args['right'] = duplicate
    with pytest.raises(ValueError, match='Right duplicate'):
        execute_data(args)
    args['join']['cardinality'] = 'm:m'
    assert len(execute_data(args).table['rows']) == 6
    args['join']['max_output_rows'] = 5
    with pytest.raises(ValueError, match='multiplication'):
        execute_data(args)


@pytest.mark.parametrize('left,right', [(['001'], [1]), ([1], [True]), ([1], [1.0])])
def test_join_never_guesses_key_type(left, right):
    with pytest.raises(ValueError, match='types differ'):
        execute_data(request('join', {'columns': ['key'], 'rows': [left]}, right={'columns': ['key'], 'rows': [right]}, join={'left_keys': ['key'], 'right_keys': ['key'], 'how': 'left', 'cardinality': '1:1'}))


def test_null_keys_do_not_match_unless_explicit():
    args = request('join', {'columns': ['id'], 'rows': [[None]]}, right={'columns': ['id'], 'rows': [[None]]}, join={'left_keys': ['id'], 'right_keys': ['id'], 'how': 'full', 'cardinality': '1:1'})
    assert len(execute_data(args).table['rows']) == 2
    args['join']['nulls_equal'] = True
    assert len(execute_data(args).table['rows']) == 1


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 10**16, {'$eval': 'x'}, ['nested']])
def test_reject_nonfinite_unbounded_and_complex_values(bad):
    with pytest.raises((ValueError, TypeError)):
        DataRequest.model_validate(request('profile', {'columns': ['a'], 'rows': [[bad]]}))


def test_strict_limits_and_unknown_fields():
    with pytest.raises(ValueError, match='Unknown'):
        DataRequest.model_validate(request('profile', shell='whoami'))
    with pytest.raises(ValueError, match='bounded rows'):
        DataRequest.model_validate(request('profile', {'columns': ['a'], 'rows': [[0]]*1001}))
    with pytest.raises(ValueError, match='Duplicate headers'):
        DataRequest.model_validate(request('profile', {'columns': ['a', 'a'], 'rows': []}))
    with pytest.raises(ValueError, match='unrelated'):
        DataRequest.model_validate(request('profile', edits=[{'cell': 'A2', 'value': 'secret'}]))


def test_explicit_cleaning_preserves_zero_ids_and_source():
    table = {'columns': ['id', 'phone', 'amount', 'label'], 'rows': [['001', '+972001', 0, '  عربي עברית  '], ['002', '0000123', None, 'English']]}
    before = deepcopy(table)
    result = execute_data(request('clean', table, cleaning=[{'operation': 'trim', 'column': 'label'}, {'operation': 'fill_missing', 'column': 'amount', 'value': 5}]))
    assert result.table['rows'] == [['001', '+972001', 0, 'عربي עברית'], ['002', '0000123', 5, 'English']]
    assert table == before
    assert result.summary['cleaning']['changes'][1]['changed_cells'] == ['C3']


def test_no_automatic_dedup_and_explicit_remove_lineage():
    table = {'columns': ['key', 'value'], 'rows': [['001', 0], ['001', 0], ['001', 1]]}
    assert profile(table)['duplicate_rows'] == 1
    result = execute_data(request('clean', table, cleaning=[{'operation': 'deduplicate', 'columns': ['key', 'value']}]))
    assert len(result.table['rows']) == 2
    assert result.summary['cleaning']['changes'][0]['removed_row_indices'] == [1]


def test_cleaning_number_separators_and_ambiguous_dates():
    table = {'columns': ['amount'], 'rows': [['1.234,50'], ['0']]}
    result = execute_data(request('clean', table, cleaning=[{'operation': 'parse_number', 'column': 'amount', 'decimal_separator': ',', 'thousands_separator': '.'}]))
    assert result.table['rows'] == [[1234.5], [0]]
    with pytest.raises(ValueError, match='date format'):
        execute_data(request('clean', {'columns': ['date'], 'rows': [['01/02/2026']]}, cleaning=[{'operation': 'parse_date', 'column': 'date'}]))
    result = execute_data(request('clean', {'columns': ['date'], 'rows': [['01/02/2026']]}, cleaning=[{'operation': 'parse_date', 'column': 'date', 'date_format': '%d/%m/%Y'}]))
    assert result.table['rows'] == [['2026-02-01']]


def test_invalid_cleaning_keep_is_explicit_and_localized():
    result = execute_data(request('clean', {'columns': ['n'], 'rows': [['not a number'], ['0']]}, cleaning=[{'operation': 'parse_number', 'column': 'n', 'on_error': 'keep'}]))
    assert result.table['rows'] == [['not a number'], [0]]
    assert result.warnings == ('Unchanged invalid input at A2',)


def test_csv_utf8_semicolon_quotes_newline_and_leading_zero():
    raw = '\ufeffid;name;value\r\n001;"عربي; עברית\nEnglish";0\r\n00000000000000001;"قال ""نعم""";\r\n'.encode('utf-8')
    result = execute_data({'operation': 'csv', 'source_format': 'csv', 'delimiter': ';'}, source_bytes=raw)
    assert result.table['rows'] == [['001', 'عربي; עברית\nEnglish', '0'], ['00000000000000001', 'قال "نعم"', '']]
    assert result.summary['encoding'] == 'utf-8-sig'
    assert result.summary['type_inference'] is False


@pytest.mark.parametrize('raw,kind', [(b'a,a\n1,2', 'duplicate_headers'), (b',b\n1,2', 'empty_or_nontext_header'), (b'', 'missing_headers')])
def test_csv_invalid_headers_are_flagged_not_renamed(raw, kind):
    result = execute_data({'operation': 'read', 'source_format': 'csv'}, source_bytes=raw)
    assert result.table is None
    assert kind in [item['kind'] for item in result.summary['header_issues']]


@pytest.mark.parametrize('raw', [b'a,b\n"unclosed,x', b'a,b\n1', b'a\n\xff', b'a\n\0'])
def test_csv_malformed_inputs_fail(raw):
    with pytest.raises(ValueError):
        execute_data({'operation': 'read', 'source_format': 'csv'}, source_bytes=raw)


def test_csv_injection_safe_mode_discloses_every_change():
    table = {'columns': ['label', 'value'], 'rows': [['=HYPERLINK("https://example.invalid")', 0], [' \t+1', None], ['-001', '=2+2'], ['001', 'ordinary']]}
    result = execute_data(request('csv', table))
    assert result.artifact.verification['prefixed_text_cells'] == ['A2', 'A3', 'A4', 'B4']
    assert result.artifact.verification['null_exported_as_empty_cells'] == ['B3']
    read = execute_data({'operation': 'read', 'source_format': 'csv'}, source_bytes=result.artifact.content)
    assert read.table['rows'][0][0].startswith("'=")
    raw = execute_data(request('csv', table, formula_safe=False))
    assert raw.artifact.verification['prefixed_text_cells'] == []
    read_raw = execute_data({'operation': 'read', 'source_format': 'csv'}, source_bytes=raw.artifact.content)
    assert read_raw.table['rows'][0][0] == table['rows'][0][0]


@pytest.mark.parametrize('formula,expected', [('=SUM(A2:A5)', '30'), ('=AVERAGE(A2:A5)', '10'), ('=COUNT(A2:A5)', '3'), ('=MIN(A2:A5)', '0'), ('=MAX(A2:A5)', '20'), ('=MEDIAN(A2:A5)', '10'), ('=($A$2+A3)*2', '20')])
def test_formula_subset_recalculates_actual_inputs(formula, expected):
    table = {'columns': ['value', 'formula'], 'rows': [[10, None], [0, None], [20, None], [None, None]], 'formulas': {'B2': formula}}
    assert recalculate(table)['calculated_values']['B2'] == expected


@pytest.mark.parametrize('formula,error', [('=A2/0', 'division_by_zero'), ('=A99+1', 'reference_outside'), ('=B2+1', 'circular_reference'), ('=IFERROR(A2/0,0)', 'unsupported_function'), ('=WEBSERVICE("https://x")', 'unsupported_formula'), ('=A3+1', 'nonnumeric_operand'), ('=__import__("os")', 'unsupported_formula')])
def test_formula_errors_are_not_hidden(formula, error):
    table = {'columns': ['input', 'result'], 'rows': [[0, None], ['10', None]], 'formulas': {'B2': formula}}
    result = recalculate(table)
    assert error in result['errors']['B2']
    assert 'B2' not in result['calculated_values']


def test_formula_patch_recalculates_dependents_and_compare_value():
    table = {'columns': ['value', 'total', 'twice'], 'rows': [[10, None, None], [0, None, None], [20, None, None]], 'formulas': {'B2': '=SUM(A2:A3)', 'C2': '=B2*2'}}
    result = execute_data(request('formulas', table, edits=[{'cell': 'B2', 'expected': '=SUM(A2:A3)', 'formula': '=SUM(A2:A4)'}]))
    assert result.summary['formula_debugging']['before']['calculated_values'] == {'B2': '10', 'C2': '20'}
    assert result.summary['formula_debugging']['after']['calculated_values'] == {'B2': '30', 'C2': '60'}
    with pytest.raises(ValueError, match='changed since'):
        execute_data(request('formulas', table, edits=[{'cell': 'B2', 'expected': '=SUM(A2:A4)', 'formula': '=SUM(A2:A4)'}]))


def test_xlsx_formula_cache_is_distinct_and_patch_preserves_parts(template):
    table = {'columns': ['id', 'amount', 'total'], 'rows': [['001', 10, None], ['002', 0, None], ['003', 20, None]], 'formulas': {'C2': '=SUM(B2:B4)'}}
    created = execute_data(request('table', table, format='xlsx', sheet_name='بيانات'), template_bytes=template).artifact.content
    stale = rewrite(created, 'xl/worksheets/sheet1.xml', lambda raw: raw.replace(b'<v>30</v>', b'<v>999</v>'))
    read = execute_data({'operation': 'read'}, source_bytes=stale)
    cell = next(c for c in read.summary['cells'] if c['address'] == 'C2')
    assert cell['formula'] == 'SUM(B2:B4)' and cell['cached_value'] == '999'
    assert read.table['rows'][0] == ['001', 10, None]
    result = execute_data({'operation': 'formulas', 'edits': [{'cell': 'B2', 'expected': 10, 'value': 11}], 'format': 'xlsx'}, source_bytes=stale)
    assert result.artifact.verification['formula_calculation']['calculated_values']['C2'] == '31'
    with ZipFile(BytesIO(stale)) as old, ZipFile(BytesIO(result.artifact.content)) as new:
        assert old.namelist() == new.namelist()
        for name in old.namelist():
            if name not in ('xl/workbook.xml', 'xl/worksheets/sheet1.xml'):
                assert old.read(name) == new.read(name)
    final = execute_data({'operation': 'read'}, source_bytes=result.artifact.content)
    assert next(c for c in final.summary['cells'] if c['address'] == 'C2')['cached_value'] == '31'


def test_xlsx_literal_formula_text_remains_inert(template):
    table = {'columns': ['id', 'text', 'value'], 'rows': [['001', '=2+2', 0], ['002', '+972123', None]]}
    result = execute_data(request('table', table, format='xlsx'), template_bytes=template)
    loaded = execute_data({'operation': 'read'}, source_bytes=result.artifact.content)
    assert loaded.table['rows'] == table['rows']
    assert loaded.table['formulas'] == {}


def test_xlsx_zip_traversal_and_dtd_rejected(template):
    with ZipFile(BytesIO(template)) as old:
        out = BytesIO()
        with ZipFile(out, 'w') as new:
            for name in old.namelist(): new.writestr(name, old.read(name))
            new.writestr('../outside', 'not executed')
    with pytest.raises(ValueError, match='Unsafe ZIP'):
        execute_data({'operation': 'read'}, source_bytes=out.getvalue())
    bad = rewrite(template, 'xl/worksheets/sheet1.xml', lambda raw: b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///private">]>'+raw)
    with pytest.raises(ValueError, match='DTD'):
        execute_data({'operation': 'read'}, source_bytes=bad)


def test_ordinary_aggregate_not_pivot_and_mixed_units_fail():
    spec = {'group_by': ['customer_id'], 'operation': 'sum', 'value_column': 'amount', 'record_unit': 'one order'}
    result = execute_data(request('aggregate', aggregate=spec))
    assert result.table['rows'] == [['C1', 10], ['C2', 20], ['C9', None]]
    assert 'not an Excel PivotTable' in result.summary['aggregation']['kind']
    table = {'columns': ['amount', 'currency'], 'rows': [[10, 'USD'], [20, 'ILS']]}
    with pytest.raises(ValueError, match='Mixed units'):
        execute_data(request('aggregate', table, aggregate={'group_by': [], 'operation': 'sum', 'value_column': 'amount', 'unit_column': 'currency', 'record_unit': 'invoice'}))


def test_statistics_empty_single_constant_weights_and_numeric_text():
    assert statistics([])['mean'] is None
    assert statistics([])['sum'] is None
    assert statistics([5])['stddev'] is None
    assert statistics([5], ddof=0)['stddev'] == '0'
    assert statistics([2, 2, 2])['stddev'] == '0'
    assert statistics([0, '10', None])['numeric_count'] == 1
    assert statistics([10, 20], weights=[1, 3])['weighted_mean'] == '17.5'
    assert statistics([10, 20], weights=[0, 0])['weighted_mean'] is None
    with pytest.raises(ValueError, match='Negative'):
        statistics([1], weights=[-1])


def test_exploration_full_and_groups_filters_no_causality():
    result = execute_data(request('explore', columns=['amount'], record_unit='one order', filters=[{'column': 'amount', 'operator': 'gte', 'value': 0}], aggregate={'group_by': ['customer_id'], 'operation': 'mean', 'value_column': 'amount', 'record_unit': 'one order'}))
    assert result.summary['filters']['excluded_row_indices'] == [3]
    assert result.summary['statistics']['amount']['mean'] == '10'
    assert result.summary['grouped_table']['rows'] == [['C1', 5], ['C2', 20]]
    assert 'no causal inference' in result.summary['interpretation']


@pytest.mark.parametrize('kind', ['bar', 'line'])
def test_native_chart_uses_real_range_and_preserves_zero_gap(template, kind):
    table = {'columns': ['الفئة', 'القيمة'], 'rows': [['صفر', 0], ['חסר', None], ['English', 20]]}
    result = execute_data(request('chart', table, chart={'kind': kind, 'category': 'الفئة', 'values': ['القيمة'], 'title': 'طلبات — הזמנות', 'unit': 'order'}), template_bytes=template)
    with ZipFile(BytesIO(result.artifact.content)) as zipped:
        chart = ET.fromstring(zipped.read('xl/charts/chart1.xml'))
        assert chart.findtext('.//c:numRef/c:f', namespaces=NS) == "'Data'!$B$2:$B$4"
        points = chart.findall('.//c:numCache/c:pt', NS)
        assert [(p.get('idx'), p.findtext('c:v', namespaces=NS)) for p in points] == [('0', '0'), ('2', '20')]
        assert chart.find('c:chart/c:dispBlanksAs', NS).get('val') == 'gap'
        assert b'pivotTable' not in zipped.read('[Content_Types].xml')
        sheet = ET.fromstring(zipped.read('xl/worksheets/sheet1.xml'))
        assert sheet.find('s:sheetPr/s:pageSetUpPr', NS).get('fitToPage') == '1'
        assert sheet.find('s:pageSetup', NS).get('fitToWidth') == '1'
        assert chart.find('.//c:ser/c:spPr/a:ln/a:solidFill/a:srgbClr', NS) is not None
        if kind == 'bar':
            assert chart.find('.//c:ser/c:spPr/a:solidFill/a:srgbClr', NS) is not None
    assert result.artifact.verification['native_editable_chart']
    assert result.artifact.verification['office_rendered'] is False


def test_source_and_typed_table_cannot_be_ambiguous(template):
    with pytest.raises(ValueError, match='not both'):
        execute_data(request('read'), source_bytes=template)


def test_no_template_no_imaginary_xlsx_output():
    with pytest.raises(ValueError, match='trusted template'):
        execute_data(request('table', format='xlsx'))


@pytest.mark.parametrize('patch', [{'operation': []}, {'format': {}}, {'chart': []}, {'table': None}, {'cleaning': None}])
def test_malformed_nested_schema_produces_validation_error(patch):
    with pytest.raises(ValueError):
        DataRequest.model_validate({**request('profile'), **patch})


def test_clean_keep_does_not_leak_failed_numeric_conversion():
    original = '1234567890123456'
    result = execute_data(request('clean', {'columns': ['n'], 'rows': [[original]]}, cleaning=[{'operation': 'parse_number', 'column': 'n', 'on_error': 'keep'}]))
    assert result.table['rows'] == [[original]]


def test_formula_errors_prevent_executable_xlsx_export(template):
    table = {'columns': ['x', 'y'], 'rows': [[1, None]], 'formulas': {'B2': '=WEBSERVICE(A2)'}}
    assert 'unsupported_function' in recalculate(table)['errors']['B2']
    with pytest.raises(ValueError, match='export blocked'):
        execute_data(request('table', table, format='xlsx'), template_bytes=template)


def test_generated_workbook_bytes_are_deterministic(template):
    first = execute_data(request('table', format='xlsx'), template_bytes=template)
    second = execute_data(request('table', format='xlsx'), template_bytes=template)
    assert first.artifact.content == second.artifact.content


def test_formula_cache_cannot_be_smuggled_as_table_value():
    with pytest.raises(ValueError, match='caches are separate'):
        execute_data(request('profile', {'columns': ['a'], 'rows': [[999]], 'formulas': {'A2': '=1+1'}}))


def test_product_chart_input_edit_refreshes_linked_native_cache(template):
    original = execute_data(request('chart', {'columns': ['category', 'amount'], 'rows': [['A', 10], ['B', 0], ['C', None]]}, chart={'kind': 'bar', 'category': 'category', 'values': ['amount'], 'title': 'Orders', 'unit': 'order'}), template_bytes=template).artifact.content
    changed = execute_data({'operation': 'formulas', 'edits': [{'cell': 'B2', 'expected': 10, 'value': 15}], 'format': 'xlsx'}, source_bytes=original)
    assert changed.artifact.verification['refreshed_chart_cache_parts'] == ['xl/charts/chart1.xml']
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(changed.artifact.content)) as after:
        old = ET.fromstring(before.read('xl/charts/chart1.xml'))
        new = ET.fromstring(after.read('xl/charts/chart1.xml'))
        assert old.findtext('.//c:numCache/c:pt/c:v', namespaces=NS) == '10'
        assert new.findtext('.//c:numCache/c:pt/c:v', namespaces=NS) == '15'
        assert new.findtext('.//c:numRef/c:f', namespaces=NS) == old.findtext('.//c:numRef/c:f', namespaces=NS)
        assert len(new.findall('.//c:numCache/c:pt', NS)) == 2


def test_csv_missing_numbers_require_explicit_cleaning_rules():
    raw = b'order_id,customer_id,amount\nO1,C1,10\nO2,C1,0\nO3,C2,20\nO4,C9,\n'
    result = execute_data({'operation': 'clean', 'source_format': 'csv', 'cleaning': [{'operation': 'empty_to_null', 'column': 'amount'}, {'operation': 'parse_number', 'column': 'amount'}]}, source_bytes=raw)
    assert result.table['rows'] == ORDERS['rows']
    assert statistics([r[2] for r in result.table['rows']])['missing'] == 1
