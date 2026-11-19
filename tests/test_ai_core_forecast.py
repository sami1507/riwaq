"""Bounded diagnostics and supplied forecasts; never fit or invent predictions."""
from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from backend.skill_artifacts import build_artifact
from backend.skill_data import execute_data, NS


@pytest.fixture
def template():
    return build_artifact('xlsx', {'title': 'Data', 'columns': [{'name': 'Value'}], 'rows': [['']]}).content


def review(table, **options):
    spec = {'time_column': 'month', 'target_column': 'sales', 'frequency': 'month', 'horizon_periods': 2,
            'validation_periods': 2, **options}
    return execute_data({'operation': 'explore', 'table': table, 'columns': ['sales'], 'forecast_review': spec})


def source_table():
    return {'columns': ['month', 'sales', 'known', 'late', 'as_of', 'known_at', 'late_at'], 'rows': [
        [f'2026-{i:02}', i*10, i, i*100, f'2026-{i:02}-01', f'2026-{i:02}-01', f'2026-{i:02}-20']
        for i in range(1, 9)]}


def chart_table(bounds=False):
    columns = ['month', 'مرصود', 'متوقع', 'source']
    rows = [[f'2026-{i:02}', value, None, 'observed ledger'] for i, value in enumerate([10, 0, 20, 25, 23, 28], 1)]
    rows += [['2026-07', None, 30, 'declared scenario v1'], ['2026-08', None, 32, 'declared scenario v1']]
    if bounds:
        columns += ['lower', 'upper']
        rows = [r+[None, None] for r in rows[:6]] + [rows[6]+[26, 34], rows[7]+[27, 37]]
    return {'columns': columns, 'rows': rows}


def chart_request(bounds=False):
    forecast = {'observed': 'مرصود', 'predicted': 'متوقع', 'source_column': 'source', 'frequency': 'month',
                'basis': 'User supplied scenario, no fitted forecast or accuracy guarantee'}
    values = ['مرصود', 'متوقع']
    if bounds:
        forecast.update(lower='lower', upper='upper', interval_basis='User supplied scenario range, not calibrated probability')
        values += ['lower', 'upper']
    return {'operation': 'chart', 'table': chart_table(bounds), 'chart': {
        'kind': 'line', 'category': 'month', 'values': values, 'title': 'المرصود والسيناريو المتوقع', 'unit': 'طلب', 'forecast': forecast}}


def chart_xml(raw):
    with ZipFile(BytesIO(raw)) as z:
        return ET.fromstring(z.read('xl/charts/chart1.xml'))


def test_three_months_do_not_support_precise_twelve_month_forecast():
    table = {'columns': ['month', 'sales'], 'rows': [['2026-01', 10], ['2026-02', 20], ['2026-03', 30]]}
    result = review(table, horizon_periods=12, validation_periods=12, seasonal_periods=12)
    detail = result.summary['forecast_review']
    assert detail['status'] == 'insufficient_data' and detail['split'] is None
    assert detail['fitted_model'] is False and detail['accuracy'] == 'not_established'
    assert {'history_shorter_than_horizon', 'insufficient_chronological_validation', 'insufficient_seasonal_cycles'} <= {x['code'] for x in detail['issues']}
    assert result.table['rows'] == table['rows'] and 'predictions' not in detail


def test_late_feature_is_excluded_and_chronological_plan_uses_available_features():
    table = source_table()
    result = review(table, prediction_time_column='as_of', features=[
        {'column': 'known', 'available_at_column': 'known_at'}, {'column': 'late', 'available_at_column': 'late_at'}])
    detail = result.summary['forecast_review']
    assert detail['eligible_features'] == ['known']
    assert detail['excluded_features'] == [{'column': 'late', 'reason': 'available_after_prediction', 'row_indices': list(range(8))}]
    assert detail['split']['training_row_indices'] == list(range(6))
    assert detail['split']['validation_row_indices'] == [6, 7]
    assert detail['split']['feature_columns'] == ['known']
    assert detail['split']['target_column'] == 'sales'
    assert detail['split']['training_table']['columns'] == ['month', 'known', 'sales']
    assert detail['split']['validation_table']['rows'] == [['2026-07', 7, 70], ['2026-08', 8, 80]]
    assert detail['fitted_model'] is False and detail['accuracy'] == 'not_established'


def test_missing_availability_never_marks_feature_safe():
    table = source_table(); table['rows'][2][5] = None
    detail = review(table, prediction_time_column='as_of', features=[{'column': 'known', 'available_at_column': 'known_at'}]).summary['forecast_review']
    assert detail['eligible_features'] == [] and detail['unresolved_features'][0]['row_indices'] == [2]
    assert detail['split']['feature_columns'] == []


def test_temporal_split_keeps_original_row_identity_and_flags_missing_period():
    table = source_table(); table['rows'] = [table['rows'][i] for i in [7, 0, 1, 2, 4, 5, 6]]
    original = deepcopy(table)
    result = review(table)
    detail = result.summary['forecast_review']
    assert detail['missing_periods'] == ['2026-04']
    assert detail['split'] is None  # Irregular sequence needs an explicit resolution.
    assert detail['ordered_row_indices'] == [1, 2, 3, 4, 5, 6, 0]
    assert result.table['rows'] == original['rows']


@pytest.mark.parametrize('bad', ['2026-13', '01/02/2026', 'not-a-date'])
def test_forecast_dates_are_explicit_not_guessed(bad):
    table = source_table(); table['rows'][0][0] = bad
    with pytest.raises(ValueError): review(table)


def test_target_cannot_be_used_as_an_available_feature():
    with pytest.raises(ValueError, match='target'):
        review(source_table(), prediction_time_column='as_of', features=[{'column': 'sales', 'available_at_column': 'known_at'}])


def test_timezone_aware_feature_availability_compares_instants():
    table = source_table()
    for row in table['rows']:
        row[4] = '2026-01-01T10:00:00+02:00'
        row[5] = '2026-01-01T08:00:00Z'
    detail = review(table, prediction_time_column='as_of', features=[{'column': 'known', 'available_at_column': 'known_at'}]).summary['forecast_review']
    assert detail['eligible_features'] == ['known']
    table['rows'][0][5] = '2026-01-01T08:00:01Z'
    detail = review(table, prediction_time_column='as_of', features=[{'column': 'known', 'available_at_column': 'known_at'}]).summary['forecast_review']
    assert detail['excluded_features'][0]['row_indices'] == [0]


@pytest.mark.parametrize('change,code', [('duplicate', 'duplicate_periods'), ('invalid_target', 'missing_or_nonnumeric_target')])
def test_unresolved_period_or_target_blocks_validation_split(change, code):
    table = source_table()
    if change == 'duplicate': table['rows'][1][0] = table['rows'][0][0]
    else: table['rows'][2][1] = 'abc'
    detail = review(table).summary['forecast_review']
    assert detail['split'] is None and code in {x['code'] for x in detail['issues']}


def test_observed_forecast_native_series_are_separate_and_keep_zero(template):
    request = chart_request()
    result = execute_data(request, template_bytes=template)
    xml = chart_xml(result.artifact.content)
    series = xml.findall('.//c:lineChart/c:ser', NS)
    assert len(series) == 2
    assert [s.findtext('c:val/c:numRef/c:f', namespaces=NS) for s in series] == ["'Data'!$B$2:$B$9", "'Data'!$C$2:$C$9"]
    assert [s.find('c:spPr/a:ln/a:prstDash', NS).get('val') for s in series] == ['solid', 'dash']
    assert [p.get('idx') for p in series[0].findall('c:val/c:numRef/c:numCache/c:pt', NS)] == ['0', '1', '2', '3', '4', '5']
    assert [p.get('idx') for p in series[1].findall('c:val/c:numRef/c:numCache/c:pt', NS)] == ['6', '7']
    assert series[0].findall('c:val/c:numRef/c:numCache/c:pt/c:v', NS)[1].text == '0'
    assert xml.find('.//c:legend', NS) is not None
    detail = result.summary['forecast_display']
    assert detail['observed_rows'] == list(range(6)) and detail['forecast_rows'] == [6, 7]
    assert detail['uncertainty'] == 'not_provided' and detail['fitted_model'] is False
    assert result.table['rows'] == request['table']['rows']


def test_supplied_ranges_have_explicit_basis_and_no_fabricated_confidence(template):
    result = execute_data(chart_request(True), template_bytes=template)
    assert len(chart_xml(result.artifact.content).findall('.//c:lineChart/c:ser', NS)) == 4
    detail = result.summary['forecast_display']
    assert detail['uncertainty'] == 'supplied_bounds_not_calibrated'
    assert detail['interval_basis'].startswith('User supplied') and 'confidence' not in detail


@pytest.mark.parametrize('mutation', ['both_values', 'forecast_before_observed', 'source_missing', 'bad_bounds', 'bounds_without_basis'])
def test_misleading_forecast_input_is_rejected_before_workbook(template, mutation):
    request = chart_request(True)
    if mutation == 'both_values': request['table']['rows'][0][2] = 999
    elif mutation == 'forecast_before_observed': request['table']['rows'][0][1], request['table']['rows'][0][2] = None, 10
    elif mutation == 'source_missing': request['table']['rows'][7][3] = ''
    elif mutation == 'bad_bounds': request['table']['rows'][7][4] = 40
    elif mutation == 'bounds_without_basis': del request['chart']['forecast']['interval_basis']
    with pytest.raises(ValueError): execute_data(request, template_bytes=template)


def test_edit_keeps_series_distinction_and_refreshes_linked_prediction(template):
    original = execute_data(chart_request(), template_bytes=template)
    changed = execute_data({'operation': 'formulas', 'source_format': 'xlsx', 'format': 'xlsx',
                            'edits': [{'cell': 'C8', 'expected': 30, 'value': 31}]}, source_bytes=original.artifact.content)
    xml = chart_xml(changed.artifact.content)
    series = xml.findall('.//c:lineChart/c:ser', NS)
    assert series[1].findtext('c:val/c:numRef/c:numCache/c:pt/c:v', namespaces=NS) == '31'
    assert series[1].find('c:spPr/a:ln/a:prstDash', NS).get('val') == 'dash'
    assert len(series[0].findall('c:val/c:numRef/c:numCache/c:pt', NS)) == 6


@pytest.mark.parametrize('edit', [{'cell': 'C8', 'expected': 30, 'value': 40}, {'cell': 'B8', 'expected': None, 'value': 5}, {'cell': 'A8', 'expected': '2026-07', 'value': '2026-09'}])
def test_edit_cannot_publish_inconsistent_forecast_roles_dates_or_bounds(template, edit):
    original = execute_data(chart_request(True), template_bytes=template)
    with pytest.raises(ValueError, match='Forecast edit'):
        execute_data({'operation': 'formulas', 'format': 'xlsx', 'edits': [edit]}, source_bytes=original.artifact.content)


def test_forecast_series_explicitly_disable_smoothing(template):
    result = execute_data(chart_request(), template_bytes=template)
    xml = chart_xml(result.artifact.content)
    assert all(n.get('val') == '0' for n in xml.findall('.//c:lineChart/c:ser/c:smooth', NS))
    assert xml.find('.//c:lineChart/c:smooth', NS).get('val') == '0'
