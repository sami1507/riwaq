"""Data contracts for AI core: deterministic values, not model-language scores."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from backend.skill_artifacts import build_artifact
from backend.skill_data import execute_data, NS


ORDERS = {'columns': ['order', 'customer', 'amount'], 'rows': [
    ['O1', 'C1', 10], ['O2', 'C1', 0], ['O3', 'C2', 20], ['O4', 'C9', None]]}
CUSTOMERS = {'columns': ['customer', 'region'], 'rows': [['C1', 'الشمال'], ['C2', 'الجنوب']]}
JOIN = {'left_keys': ['customer'], 'right_keys': ['customer'], 'how': 'left', 'cardinality': 'm:1'}


@pytest.fixture
def template():
    return build_artifact('xlsx', {'title': 'Data', 'columns': [{'name': 'Value'}], 'rows': [['']]}).content


def run(operation, table, **options):
    return execute_data({'operation': operation, 'table': deepcopy(table), **options})


def chart_xml(content):
    with ZipFile(BytesIO(content)) as package:
        return ET.fromstring(package.read('xl/charts/chart1.xml'))


def test_csv_decimal_comma_quoted_semicolon_and_newline_preserve_records():
    raw = 'id;amount;note\n001;10,50;"سطر; واحد\nتابع"\n002;0;التالي\n'.encode()
    read = execute_data({'operation': 'read', 'source_format': 'csv', 'delimiter': ';'}, source_bytes=raw)
    assert read.table['rows'] == [['001', '10,50', 'سطر; واحد\nتابع'], ['002', '0', 'التالي']]
    cleaned = run('clean', read.table, cleaning=[{'operation': 'parse_number', 'column': 'amount', 'decimal_separator': ','}])
    assert cleaned.table['rows'] == [['001', 10.5, 'سطر; واحد\nتابع'], ['002', 0, 'التالي']]
    assert cleaned.summary['cleaning']['after']['rows'] == 2


def test_structured_zero_null_empty_remain_distinct():
    table = {'columns': ['amount', 'missing', 'note'], 'rows': [[0, None, '']]}
    result = run('profile', table)
    assert json.loads(json.dumps(result.table))['rows'] == [[0, None, '']]
    fields = result.summary['profile']['fields']
    assert fields['amount']['numeric_count'] == 1 and fields['amount']['missing'] == 0
    assert fields['missing']['missing'] == 1 and fields['note']['empty_text'] == 1


@pytest.mark.parametrize('quantity,decimal,thousands,expected', [
    ('١٢', '.', '', 12), ('۱۲', '.', '', 12), ('١٬٢٣٤٫٥٠', '٫', '٬', 1234.5), ('١٢,٥', ',', '', 12.5)])
def test_explicit_arabic_quantity_does_not_normalize_text_identifier(quantity, decimal, thousands, expected):
    table = {'columns': ['id', 'amount'], 'rows': [['٠١٢', quantity]]}
    result = run('clean', table, cleaning=[{'operation': 'parse_number', 'column': 'amount', 'decimal_separator': decimal, 'thousands_separator': thousands}])
    assert result.table['rows'] == [['٠١٢', expected]]
    assert table['rows'] == [['٠١٢', quantity]]


@pytest.mark.parametrize('value', ['0.123456789012344999', '0.' + '0'*323 + '1'])
def test_numeric_text_conversion_never_rounds_or_underflows_silently(value):
    table = {'columns': ['amount'], 'rows': [[value]]}
    with pytest.raises(ValueError, match='Cleaning failed at A2'):
        run('clean', table, cleaning=[{'operation': 'parse_number', 'column': 'amount'}])
    retained = run('clean', table, cleaning=[{'operation': 'parse_number', 'column': 'amount', 'on_error': 'keep'}])
    assert retained.table['rows'] == [[value]]
    assert retained.warnings == ('Unchanged invalid input at A2',)


def test_long_trailing_zero_quantity_has_no_false_precision_error():
    result = run('clean', {'columns': ['amount'], 'rows': [['0.1000000000000000000000']]}, cleaning=[{'operation': 'parse_number', 'column': 'amount'}])
    assert result.table['rows'] == [[0.1]]


@pytest.mark.parametrize('raw', [b'PK\x03\x04\x00\x00archive', b'amount\n\x80\xff', b'amount\n\x00'])
def test_csv_binary_or_archive_disguised_as_text_is_rejected(raw):
    with pytest.raises(ValueError):
        execute_data({'operation': 'read', 'source_format': 'csv'}, source_bytes=raw)


def test_reference_orders_statistics_and_left_join_keep_zero_missing_and_unmatched():
    result = run('stats', ORDERS, columns=['amount'])
    stats = result.summary['statistics']['amount']
    assert {k: stats[k] for k in ['count', 'numeric_count', 'missing', 'sum', 'mean', 'median']} == {
        'count': 4, 'numeric_count': 3, 'missing': 1, 'sum': '30', 'mean': '10', 'median': '10'}
    joined = run('join', ORDERS, right=CUSTOMERS, join=JOIN)
    assert len(joined.table['rows']) == 4 and joined.summary['join']['unmatched_left'] == 1
    assert joined.table['rows'][1][2] == 0 and joined.table['rows'][3][2] is None


def test_duplicate_reference_join_rejects_inflated_total_and_explicit_many_many_reports_it():
    duplicate = deepcopy(CUSTOMERS); duplicate['rows'].append(['C1', 'آخر'])
    with pytest.raises(ValueError, match='Right duplicate keys'):
        run('join', ORDERS, right=duplicate, join=JOIN)
    raw = run('join', ORDERS, right=duplicate, join={**JOIN, 'cardinality': 'm:m'})
    assert raw.summary['join']['predicted_rows'] == raw.summary['join']['actual_rows'] == 6
    assert run('stats', raw.table, columns=['amount']).summary['statistics']['amount']['sum'] == '40'
    assert raw.summary['join']['right_duplicate_key_rows'] == 2


def test_invalid_amount_is_reported_not_silently_zeroed():
    table = deepcopy(ORDERS); table['rows'][2][2] = 'abc'
    result = run('stats', table, columns=['amount'])
    stats = result.summary['statistics']['amount']
    assert stats['sum'] == '10' and stats['numeric_count'] == 2 and stats['nonnumeric'] == 1
    assert stats['nonnumeric_row_indices'] == [2]
    assert any('nonnumeric values excluded' in warning for warning in result.warnings)
    assert result.table['rows'][2][2] == 'abc'
    with pytest.raises(ValueError, match='Cleaning failed at C4'):
        run('clean', table, cleaning=[{'operation': 'parse_number', 'column': 'amount'}])


def test_weighted_group_means_are_26_not_unweighted_50():
    table = {'columns': ['group', 'mean', 'n'], 'rows': [['A', 80, 10], ['B', 20, 90]]}
    stats = run('stats', table, columns=['mean'], weight_column='n').summary['statistics']['mean']
    assert stats['weighted_mean'] == '26' and stats['weight_sum'] == '100' and stats['mean'] == '50'
    grouped = run('aggregate', table, aggregate={'group_by': [], 'operation': 'weighted_mean', 'value_column': 'mean', 'weight_column': 'n', 'record_unit': 'group with sample count'})
    assert grouped.table['rows'] == [[26]]


def test_outlier_is_retained_and_mean_median_computed_independently():
    table = {'columns': ['value'], 'rows': [[1], [2], [3], [100]]}
    result = run('stats', table, columns=['value'])
    assert result.table['rows'] == table['rows']
    assert result.summary['statistics']['value']['mean'] == '26.5'
    assert result.summary['statistics']['value']['median'] == '2.5'


def test_corrected_orders_recompute_statistics_table_chart_and_saved_workbook(template):
    table = deepcopy(ORDERS); table['rows'][2][2] = 30
    stats = run('stats', table, columns=['amount']).summary['statistics']['amount']
    assert stats['sum'] == '40' and stats['numeric_count'] == 3
    assert Decimal(stats['mean']) == Decimal('13.33333333333333333333333333333333')
    spec = {'kind': 'bar', 'category': 'order', 'values': ['amount'], 'title': 'قيمة الطلبات', 'unit': 'وحدة'}
    result = execute_data({'operation': 'chart', 'table': table, 'chart': spec}, template_bytes=template)
    chart = chart_xml(result.artifact.content)
    assert [v.text for v in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v', NS)] == ['10', '0', '30']
    reopened = execute_data({'operation': 'read', 'source_format': 'xlsx'}, source_bytes=result.artifact.content)
    assert reopened.table['rows'] == table['rows']
    assert run('stats', reopened.table, columns=['amount']).summary['statistics']['amount']['sum'] == '40'


def test_arabic_chart_keeps_zero_category_and_has_explicit_zero_baseline(template):
    table = {'columns': ['الفئة', 'القيمة'], 'rows': [['الأولى', 10], ['الثانية', 0], ['الثالثة', 20]]}
    result = execute_data({'operation': 'chart', 'table': table, 'chart': {'kind': 'bar', 'category': 'الفئة', 'values': ['القيمة'], 'title': 'الفئات', 'unit': 'وحدة'}}, template_bytes=template)
    chart = chart_xml(result.artifact.content)
    assert [v.text for v in chart.findall('.//c:cat/c:strRef/c:strCache/c:pt/c:v', NS)] == ['الأولى', 'الثانية', 'الثالثة']
    assert [v.text for v in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v', NS)] == ['10', '0', '20']
    assert chart.find('.//c:valAx/c:scaling/c:min', NS).get('val') == '0'


def test_close_bar_values_use_honest_zero_baseline(template):
    table = {'columns': ['label', 'value'], 'rows': [['A', 50], ['B', 51]]}
    result = execute_data({'operation': 'chart', 'table': table, 'chart': {'kind': 'bar', 'category': 'label', 'values': ['value'], 'title': 'Comparison', 'unit': 'units'}}, template_bytes=template)
    assert chart_xml(result.artifact.content).find('.//c:valAx/c:scaling/c:min', NS).get('val') == '0'


def test_chart_thousands_display_scales_axis_not_source_values(template):
    table = {'columns': ['label', 'amount'], 'rows': [['A', 10000], ['B', 20000]]}
    result = execute_data({'operation': 'chart', 'table': table, 'chart': {'kind': 'bar', 'category': 'label', 'values': ['amount'], 'title': 'القيمة بالآلاف', 'unit': 'ILS', 'display_unit': 'thousands'}}, template_bytes=template)
    chart = chart_xml(result.artifact.content)
    assert chart.find('.//c:valAx/c:dispUnits/c:builtInUnit', NS).get('val') == 'thousands'
    assert [v.text for v in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v', NS)] == ['10000', '20000']
    reopened = execute_data({'operation': 'read', 'source_format': 'xlsx'}, source_bytes=result.artifact.content)
    assert reopened.table['rows'] == table['rows']


@pytest.mark.parametrize('values,minimum,maximum', [([-10, 0, 20], '-10', None), ([-10, -2], '-10', '0'), ([0, 0], '0', '1')])
def test_bar_scaling_does_not_hide_negative_or_zero_only_data(template, values, minimum, maximum):
    table = {'columns': ['label', 'value'], 'rows': [[str(i), value] for i, value in enumerate(values)]}
    result = execute_data({'operation': 'chart', 'table': table, 'chart': {'kind': 'bar', 'category': 'label', 'values': ['value'], 'title': 'Values', 'unit': 'units'}}, template_bytes=template)
    scaling = chart_xml(result.artifact.content).find('.//c:valAx/c:scaling', NS)
    assert scaling.find('c:min', NS).get('val') == minimum
    top = scaling.find('c:max', NS)
    assert (top.get('val') if top is not None else None) == maximum


def test_chart_edit_updates_scale_when_positive_value_becomes_negative(template):
    table = {'columns': ['label', 'value'], 'rows': [['A', 50], ['B', 51]]}
    original = execute_data({'operation': 'chart', 'table': table, 'chart': {'kind': 'bar', 'category': 'label', 'values': ['value'], 'title': 'Values', 'unit': 'units'}}, template_bytes=template)
    edited = execute_data({'operation': 'formulas', 'source_format': 'xlsx', 'format': 'xlsx', 'edits': [{'cell': 'B2', 'expected': 50, 'value': -20}]}, source_bytes=original.artifact.content)
    chart = chart_xml(edited.artifact.content)
    assert chart.find('.//c:valAx/c:scaling/c:min', NS).get('val') == '-20'
    assert [v.text for v in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v', NS)] == ['-20', '51']


def test_display_unit_typo_is_rejected_before_file_creation(template):
    with pytest.raises(ValueError, match='display unit'):
        execute_data({'operation': 'chart', 'table': ORDERS, 'chart': {'kind': 'bar', 'category': 'order', 'values': ['amount'], 'title': 'Values', 'unit': 'USD', 'display_unit': 'thousand'}}, template_bytes=template)


def test_saved_formula_updates_after_input_change_without_replacing_formula(template):
    table = {'columns': ['input', 'double'], 'rows': [[10, None]], 'formulas': {'B2': '=A2*2'}}
    original = execute_data({'operation': 'read', 'table': table, 'format': 'xlsx'}, template_bytes=template)
    assert original.artifact.verification['formula_calculation']['calculated_values']['B2'] == '20'
    edited = execute_data({'operation': 'formulas', 'source_format': 'xlsx', 'format': 'xlsx', 'edits': [{'cell': 'A2', 'expected': 10, 'value': 15}]}, source_bytes=original.artifact.content)
    assert edited.artifact.verification['formula_calculation']['calculated_values']['B2'] == '30'
    reopened = execute_data({'operation': 'read', 'source_format': 'xlsx'}, source_bytes=edited.artifact.content)
    assert reopened.table['formulas'] == {'B2': '=A2*2'}
    assert reopened.table['rows'] == [[15, None]]


def test_xlsx_unrepresentable_numeric_source_is_preserved_as_text_with_warning(template):
    raw = execute_data({'operation': 'read', 'table': {'columns': ['amount'], 'rows': [[1]]}, 'format': 'xlsx'}, template_bytes=template).artifact.content
    out = BytesIO()
    with ZipFile(BytesIO(raw)) as source, ZipFile(out, 'w', ZIP_DEFLATED) as dest:
        for name in source.namelist():
            content = source.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                tree = ET.fromstring(content)
                tree.find(".//s:c[@r='A2']/s:v", NS).text = '1e-324'
                content = ET.tostring(tree)
            dest.writestr(name, content)
    result = execute_data({'operation': 'read', 'source_format': 'xlsx'}, source_bytes=out.getvalue())
    assert result.table['rows'] == [['1e-324']]
    assert result.summary['warnings'] == ['Numeric value retained as raw text at A2']
