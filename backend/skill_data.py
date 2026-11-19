"""Bounded data transformations. Standard library only, no I/O or authority.

Callers resolve ACL/versioned input references and isolate untrusted ZIP parsing.
The worker repeats validation. A trusted blank XLSX from skill_artifacts is the
template for new outputs; existing workbooks are edited only in explicit cells.
No formula evaluation delegates to Python eval, a shell, SQL or an Office process.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation, localcontext
import csv
import hashlib
from io import BytesIO, StringIO
import json
import math
import re
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

VERSION = 'data-v5'
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS, MAX_COLS, MAX_CELLS, MAX_JSON = 1000, 26, 20000, 200000
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
XDR = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS = {'s': S, 'r': R, 'c': C, 'a': A, 'xdr': XDR}
OPERATIONS = {'read', 'csv', 'clean', 'formulas', 'join', 'aggregate', 'chart', 'profile', 'stats', 'explore', 'table'}
NUMERIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
FORECAST_LABELS = ['Observed / مرصود', 'Forecast / متوقع', 'Supplied lower / حد أدنى مقدم', 'Supplied upper / حد أعلى مقدم']


def require(ok, message):
    if not ok:
        raise ValueError(message)


def keys(value, allowed, required=()):
    require(type(value) is dict and not set(value) - set(allowed), 'Unknown or invalid object fields')
    require(not set(required) - set(value), 'Required fields missing')


def text(value, maximum=2000, *, empty=True):
    require(type(value) is str and len(value) <= maximum and (empty or value != ''), 'Invalid bounded text')
    require(not any((ord(c) < 32 and c not in '\t\r\n') or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value), 'Unsupported control character')
    return value


def scalar(value):
    require(value is None or type(value) in (str, int, float, bool), 'Only scalar cell values accepted')
    if type(value) is str:
        text(value)
    elif type(value) in (int, float):
        require(math.isfinite(value) and abs(value) <= 10**14, 'Finite bounded number required')
        require(len(Decimal(str(value)).as_tuple().digits) <= 15, 'Use text for identifiers longer than 15 significant digits')
    return value


def number(value):
    return Decimal(str(value)) if type(value) in (int, float, Decimal) else None


def _native_number(value):
    """Convert an explicitly numeric source only when its value survives JSON.

    Checking precision after float() would hide rounding or underflow. Keep
    unsupported values as source text (reader) or follow the cleaning error
    policy; this must never silently turn a nonzero quantity into zero.
    """
    require(value.is_finite() and abs(value) <= 10**14, 'Finite bounded number required')
    digits = list(value.as_tuple().digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
    require(len(digits) <= 15, 'Numeric value exceeds 15 significant digits; retain exact text')
    candidate = int(value) if value == value.to_integral_value() else float(value)
    scalar(candidate)
    require(Decimal(str(candidate)) == value, 'Numeric conversion would lose precision; retain exact text')
    return candidate


def decstr(value):
    if value is None:
        return None
    require(value.is_finite() and value.adjusted() <= 100, 'Calculation outside finite bounds')
    return '0' if value == 0 else format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def _typed_measure(value):
    """Use a native numeric cell only within the declared Excel-safe precision."""
    if value is None or type(value) is not str:
        return value
    try:
        return _native_number(Decimal(value))
    except (ValueError, InvalidOperation):
        pass
    return value


def addr(row, col):
    return chr(65 + col) + str(row)


def address(value):
    require(type(value) is str and re.fullmatch(r'\$?[A-Z]\$?[1-9][0-9]{0,3}', value), 'Only A1:Z1001 cell addresses are supported')
    normalized = value.replace('$', '')
    require(int(normalized[1:]) <= MAX_ROWS + 1, 'Cell address exceeds supported rows')
    return normalized


def validate_table(value):
    keys(value, {'columns', 'rows', 'formulas'}, {'columns', 'rows'})
    columns, rows = value['columns'], value['rows']
    require(type(columns) is list and 1 <= len(columns) <= MAX_COLS, 'Table requires 1–26 columns')
    for name in columns:
        text(name, 100, empty=False)
    require(len(set(columns)) == len(columns), 'Duplicate headers must be resolved explicitly')
    require(type(rows) is list and len(rows) <= MAX_ROWS and len(rows) * len(columns) <= MAX_CELLS, 'Table exceeds bounded rows/cells')
    for row in rows:
        require(type(row) is list and len(row) == len(columns), 'Row width must match columns')
        for cell_value in row:
            scalar(cell_value)
    formulas = value.get('formulas', {})
    require(type(formulas) is dict and len(formulas) <= 500, 'Formula count exceeds bound')
    for location, formula in formulas.items():
        normalized = address(location)
        require(normalized == location and 2 <= int(location[1:]) <= len(rows) + 1 and ord(location[0]) - 65 < len(columns), 'Formula must target an existing data cell')
        text(formula, 1000, empty=False)
        require(rows[int(location[1:])-2][ord(location[0])-65] is None, 'Formula cells use null values; raw/stored caches are separate metadata')
    return {'columns': list(columns), 'rows': [list(r) for r in rows], 'formulas': dict(formulas)}


@dataclass(frozen=True)
class DataRequest:
    """Small stdlib schema shared with the sandbox, not a Pydantic dependency."""
    value: dict

    @classmethod
    def model_validate(cls, value):
        return cls(validate_request(value))

    def model_dump(self, **_):
        return json.loads(json.dumps(self.value, ensure_ascii=False, allow_nan=False))


def validate_request(value):
    try:
        return _validate_request(value)
    except (TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('Malformed or excessively nested data request') from exc


def _validate_request(value):
    if isinstance(value, DataRequest):
        value = value.model_dump()
    keys(value, {'operation', 'table', 'right', 'sourceId', 'source_format', 'format', 'sheet_name', 'sheet_index', 'header_row', 'delimiter', 'formula_safe', 'cleaning', 'edits', 'join', 'aggregate', 'chart', 'columns', 'ddof', 'quantiles', 'weight_column', 'record_unit', 'filters', 'forecast_review'}, {'operation'})
    require(value['operation'] in OPERATIONS, 'Unknown data operation')
    require(len(json.dumps(value, ensure_ascii=False, allow_nan=False)) <= MAX_JSON, 'Data request exceeds 200000 characters')
    result = dict(value)
    if 'sourceId' in result:
        text(result['sourceId'], 128, empty=False)
    for name in ('table', 'right'):
        if name in result:
            result[name] = validate_table(result[name])
    for name, allowed, default in [('format', {'json', 'csv', 'xlsx'}, 'json'), ('source_format', {'xlsx', 'csv'}, 'xlsx')]:
        result.setdefault(name, default)
        require(result[name] in allowed, 'Unsupported ' + name)
    result.setdefault('sheet_name', 'Data')
    text(result['sheet_name'], 31, empty=False)
    require(not re.search(r"[\\/*?:\[\]]", result['sheet_name']) and not result['sheet_name'].startswith("'") and not result['sheet_name'].endswith("'"), 'Invalid sheet name')
    for name, default, low, high in [('sheet_index', 0, 0, 199), ('header_row', 1, 1, 1000), ('ddof', 1, 0, 1)]:
        result.setdefault(name, default)
        require(type(result[name]) is int and low <= result[name] <= high, 'Invalid ' + name)
    result.setdefault('delimiter', ',')
    require(result['delimiter'] in (',', ';', '\t', '|'), 'Explicit supported delimiter required')
    result.setdefault('formula_safe', True)
    require(type(result['formula_safe']) is bool, 'formula_safe must be boolean')
    result.setdefault('quantiles', [0.25, 0.5, 0.75])
    require(type(result['quantiles']) is list and len(result['quantiles']) <= 10 and all(type(q) in (int, float) and math.isfinite(q) and 0 <= q <= 1 for q in result['quantiles']), 'Invalid quantiles')
    result.setdefault('columns', [])
    require(type(result['columns']) is list and len(result['columns']) <= MAX_COLS, 'Invalid selected columns')
    for name in result['columns']:
        text(name, 100, empty=False)
    require(len(set(result['columns'])) == len(result['columns']), 'Duplicate selected column')
    for name in ('record_unit', 'weight_column'):
        if name in result:
            text(result[name], 100, empty=False)
    for name, maximum in [('cleaning', 20), ('edits', 100), ('filters', 20)]:
        result.setdefault(name, [])
        require(type(result[name]) is list and len(result[name]) <= maximum, 'Too many ' + name)
    for rule in result['cleaning']:
        keys(rule, {'operation', 'column', 'value', 'columns', 'decimal_separator', 'thousands_separator', 'date_format', 'on_error'}, {'operation'})
        require(rule['operation'] in ('trim', 'parse_number', 'parse_date', 'fill_missing', 'empty_to_null', 'rename', 'deduplicate'), 'Unsupported cleaning rule')
        if rule['operation'] != 'deduplicate':
            text(rule.get('column'), 100, empty=False)
        if 'value' in rule:
            scalar(rule['value'])
        if rule['operation'] in ('rename', 'fill_missing'):
            require('value' in rule, 'Explicit cleaning value required')
        if rule['operation'] == 'deduplicate':
            require(type(rule.get('columns')) is list and 1 <= len(rule['columns']) <= MAX_COLS, 'Explicit deduplication keys required')
        require(rule.get('on_error', 'reject') in ('reject', 'keep'), 'Unknown parse error policy')
        if rule['operation'] == 'parse_date':
            require(rule.get('date_format') in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'), 'Explicit supported date format required; ambiguous dates are not guessed')
        if rule['operation'] == 'parse_number':
            require(rule.get('decimal_separator', '.') in ('.', ',', '٫') and rule.get('thousands_separator', '') in ('', ',', '.', ' ', '٬'), 'Invalid numeric separators')
            require(rule.get('decimal_separator', '.') != rule.get('thousands_separator', ''), 'Separators must differ')
    for edit in result['edits']:
        keys(edit, {'cell', 'value', 'formula', 'expected'}, {'cell'})
        address(edit['cell'])
        require(('value' in edit) != ('formula' in edit), 'Each edit needs value or formula, not both')
        if 'formula' in edit:
            text(edit['formula'], 1000, empty=False)
        else:
            scalar(edit['value'])
        if 'expected' in edit:
            scalar(edit['expected'])
    for rule in result['filters']:
        keys(rule, {'column', 'operator', 'value'}, {'column', 'operator', 'value'})
        text(rule['column'], 100, empty=False)
        require(rule['operator'] in ('eq', 'ne', 'gte', 'lte'), 'Unsupported filter')
        scalar(rule['value'])
    if 'join' in result:
        spec = result['join']
        keys(spec, {'left_keys', 'right_keys', 'how', 'cardinality', 'nulls_equal', 'max_output_rows'}, {'left_keys', 'right_keys', 'how', 'cardinality'})
        require(spec['how'] in ('left', 'inner', 'full') and spec['cardinality'] in ('1:1', '1:m', 'm:1', 'm:m'), 'Explicit join type/cardinality required')
        require(type(spec['left_keys']) is list and type(spec['right_keys']) is list and 1 <= len(spec['left_keys']) == len(spec['right_keys']) <= 4, 'Join keys must correspond')
        for name in spec['left_keys'] + spec['right_keys']:
            text(name, 100, empty=False)
        require(type(spec.get('nulls_equal', False)) is bool, 'Invalid null join policy')
        require(type(spec.get('max_output_rows', 1000)) is int and 1 <= spec.get('max_output_rows', 1000) <= MAX_ROWS, 'Invalid join row bound')
    if 'aggregate' in result:
        spec = result['aggregate']
        keys(spec, {'group_by', 'value_column', 'operation', 'weight_column', 'unit_column', 'record_unit'}, {'group_by', 'operation', 'record_unit'})
        require(type(spec['group_by']) is list and len(spec['group_by']) <= 4 and len(set(spec['group_by'])) == len(spec['group_by']), 'Invalid aggregation dimensions')
        for name in spec['group_by']:
            text(name, 100, empty=False)
        require(spec['operation'] in ('sum', 'mean', 'count', 'distinct', 'weighted_mean'), 'Unsupported aggregation')
        for name in ('value_column', 'weight_column', 'unit_column', 'record_unit'):
            if name in spec:
                text(spec[name], 100, empty=False)
        require(spec['operation'] == 'count' or 'value_column' in spec, 'Aggregation needs a value column')
        require(spec['operation'] != 'weighted_mean' or 'weight_column' in spec, 'Weights required')
    if 'chart' in result:
        spec = result['chart']
        keys(spec, {'kind', 'category', 'values', 'title', 'unit', 'display_unit', 'forecast'}, {'kind', 'category', 'values', 'title', 'unit'})
        require(spec['kind'] in ('bar', 'line'), 'Supported chart types: bar, line')
        require(spec.get('display_unit', 'units') in ('units', 'thousands', 'millions'), 'Unsupported chart display unit')
        require(type(spec['values']) is list and 1 <= len(spec['values']) <= 4, 'Chart requires 1–4 series')
        for val in [spec['category'], spec['title'], spec['unit']] + spec['values']:
            text(val, 150, empty=False)
        if 'forecast' in spec:
            forecast = spec['forecast']
            keys(forecast, {'observed', 'predicted', 'source_column', 'frequency', 'basis', 'lower', 'upper', 'interval_basis'}, {'observed', 'predicted', 'source_column', 'frequency', 'basis'})
            require(spec['kind'] == 'line', 'Observed/forecast separation currently requires a line chart')
            require(forecast['frequency'] in ('month', 'day'), 'Explicit month/day frequency required')
            for key in ('observed', 'predicted', 'source_column', 'basis', 'lower', 'upper', 'interval_basis'):
                if key in forecast: require(bool(text(forecast[key], 500 if key.endswith('basis') else 100, empty=False).strip()), 'Forecast metadata cannot be blank')
            bounds = any(k in forecast for k in ('lower', 'upper', 'interval_basis'))
            require(not bounds or all(k in forecast for k in ('lower', 'upper', 'interval_basis')), 'Bounds require lower, upper and an explicit interval basis')
            expected_series = [forecast['observed'], forecast['predicted']] + ([forecast['lower'], forecast['upper']] if bounds else [])
            require(len(set(expected_series)) == len(expected_series) and spec['values'] == expected_series, 'Forecast series must identify distinct observed, predicted and optional bound columns in order')
    if 'forecast_review' in result:
        spec = result['forecast_review']
        keys(spec, {'time_column', 'target_column', 'frequency', 'horizon_periods', 'validation_periods', 'seasonal_periods', 'prediction_time_column', 'features'}, {'time_column', 'target_column', 'frequency', 'horizon_periods', 'validation_periods'})
        require(result['operation'] == 'explore', 'Forecast diagnostics require exploratory analysis')
        require(spec['frequency'] in ('month', 'day'), 'Explicit month/day frequency required')
        for key in ('time_column', 'target_column', 'prediction_time_column'):
            if key in spec: text(spec[key], 100, empty=False)
        for key in ('horizon_periods', 'validation_periods', 'seasonal_periods'):
            if key in spec: require(type(spec[key]) is int and 1 <= spec[key] <= 366, 'Bounded positive forecast period counts required')
        features = spec.get('features', [])
        require(type(features) is list and len(features) <= 20, 'At most 20 declared features supported')
        require(not features or 'prediction_time_column' in spec, 'Feature availability requires a prediction-time column')
        names = []
        for feature in features:
            keys(feature, {'column', 'available_at_column'}, {'column', 'available_at_column'})
            for key in feature: text(feature[key], 100, empty=False)
            require(feature['column'] != spec['target_column'], 'The target cannot be used as a feature')
            names.append(feature['column'])
        require(len(names) == len(set(names)), 'Duplicate declared features')
    expected = {'clean': 'cleaning', 'formulas': 'edits', 'join': 'join', 'aggregate': 'aggregate', 'chart': 'chart'}
    if result['operation'] in expected:
        require(bool(result.get(expected[result['operation']])), 'Operation options required')
    require(not result['cleaning'] or result['operation'] == 'clean', 'Cleaning rules on unrelated operation')
    require(not result['edits'] or result['operation'] == 'formulas', 'Edits on unrelated operation')
    require('join' not in result or result['operation'] == 'join', 'Join options on unrelated operation')
    require('right' not in result or result['operation'] == 'join', 'Right table requires join')
    require('chart' not in result or result['operation'] == 'chart', 'Chart options on unrelated operation')
    return result


@dataclass(frozen=True)
class DataArtifact:
    content: bytes
    filename: str
    media_type: str
    sha256: str
    verification: dict
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class DataResult:
    table: dict | None
    summary: dict
    artifact: DataArtifact | None = None
    warnings: tuple[str, ...] = ()
    source_sha256: str | None = None
    engine_version: str = VERSION


def _column(table, name):
    require(name in table['columns'], 'Unknown column: ' + str(name))
    return table['columns'].index(name)


def _key(values):
    return tuple((type(value).__name__, value) for value in values)


def profile(table):
    table = validate_table(table)
    result = {'scope': 'all bounded rows; not a sample', 'rows': len(table['rows']), 'columns': len(table['columns']), 'duplicate_rows': len(table['rows']) - len({_key(r) for r in table['rows']}), 'fields': {}}
    for index, name in enumerate(table['columns']):
        values = [row[index] for row in table['rows']]
        nums = [number(v) for v in values if number(v) is not None]
        result['fields'][name] = {'count': len(values), 'missing': sum(v is None for v in values), 'empty_text': sum(v == '' for v in values), 'numeric_count': len(nums), 'types': dict(Counter('null' if v is None else type(v).__name__ for v in values)), 'unique_nonmissing': len({_key([v]) for v in values if v is not None}), 'min': decstr(min(nums)) if nums else None, 'max': decstr(max(nums)) if nums else None}
    return result


def _period(value, frequency):
    """Strict source periods; no locale/timezone inference or server clock."""
    require(type(value) is str, 'Forecast periods require explicit ISO text')
    if frequency == 'month':
        require(re.fullmatch(r'[0-9]{4}-[0-9]{2}', value) is not None, 'Monthly periods require YYYY-MM')
        parsed = date.fromisoformat(value+'-01')
        return parsed.year*12+parsed.month-1
    require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is not None, 'Daily periods require YYYY-MM-DD')
    return date.fromisoformat(value).toordinal()


def _period_label(value, frequency):
    return f'{value//12:04}-{value%12+1:02}' if frequency == 'month' else date.fromordinal(value).isoformat()


def _availability_time(value):
    text(value, 40, empty=False)
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return 'date', date.fromisoformat(value)
    require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value) is not None, 'Availability requires ISO dates or timezone-aware instants')
    return 'instant', datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def forecast_review(table, spec):
    """Diagnostics and a declared chronological plan, never a fitted forecast.

    Minimum-history/seasonality checks are stated conservative heuristics, not
    statistical guarantees. Feature availability is checked only as supplied.
    """
    time_index, target = _column(table, spec['time_column']), _column(table, spec['target_column'])
    frequency = spec['frequency']
    periods = [_period(row[time_index], frequency) for row in table['rows']]
    ordered = sorted(range(len(periods)), key=lambda i: (periods[i], i))
    unique = sorted(set(periods)); counts = Counter(periods)
    require(not unique or unique[-1]-unique[0] <= 2000, 'Forecast period span exceeds diagnostic bound')
    missing = [_period_label(i, frequency) for i in range(unique[0], unique[-1]+1) if i not in counts] if unique else []
    duplicates = [i for i in ordered if counts[periods[i]] > 1]
    invalid_targets = [i for i, row in enumerate(table['rows']) if number(row[target]) is None]
    issues = []
    def issue(code, **detail): issues.append({'code': code, **detail})
    horizon, validation = spec['horizon_periods'], spec['validation_periods']
    minimum_training = max(2, horizon)
    if len(unique) < horizon: issue('history_shorter_than_horizon', history_periods=len(unique), horizon_periods=horizon)
    if len(unique) < minimum_training+validation: issue('insufficient_chronological_validation', minimum_training_periods=minimum_training, validation_periods=validation)
    if validation < horizon: issue('validation_shorter_than_requested_horizon', validation_periods=validation, horizon_periods=horizon)
    if spec.get('seasonal_periods') and len(unique) < 2*spec['seasonal_periods']:
        issue('insufficient_seasonal_cycles', available_periods=len(unique), declared_cycle_periods=spec['seasonal_periods'], diagnostic_minimum_cycles=2)
    if missing: issue('missing_periods', periods=missing)
    if duplicates: issue('duplicate_periods', row_indices=duplicates)
    if invalid_targets: issue('missing_or_nonnumeric_target', row_indices=invalid_targets)
    eligible, excluded, unresolved = [], [], []
    for feature in spec.get('features', []):
        _column(table, feature['column'])
        available = _column(table, feature['available_at_column'])
        cutoff = _column(table, spec['prediction_time_column'])
        late, unknown = [], []
        for i, row in enumerate(table['rows']):
            if row[available] in (None, '') or row[cutoff] in (None, ''):
                unknown.append(i); continue
            observed, prediction = _availability_time(row[available]), _availability_time(row[cutoff])
            require(observed[0] == prediction[0], 'Cannot compare date-only availability with an instant')
            if observed[1] > prediction[1]: late.append(i)
        if late:
            excluded.append({'column': feature['column'], 'reason': 'available_after_prediction', 'row_indices': late})
        if unknown:
            unresolved.append({'column': feature['column'], 'reason': 'availability_unknown', 'row_indices': unknown})
        if not late and not unknown: eligible.append(feature['column'])
    if excluded: issue('future_feature_leakage', columns=[x['column'] for x in excluded])
    if unresolved: issue('unresolved_feature_availability', columns=[x['column'] for x in unresolved])
    split = None
    if len(unique) >= minimum_training+validation and not missing and not duplicates and not invalid_targets:
        selected_columns = list(dict.fromkeys([spec['time_column'], *eligible, spec['target_column']]))
        selected_indices = [_column(table, name) for name in selected_columns]
        def subset(indices):
            return {'columns': selected_columns, 'rows': [[table['rows'][i][j] for j in selected_indices] for i in indices]}
        split = {'method': 'chronological_holdout', 'training_row_indices': ordered[:-validation],
                 'validation_row_indices': ordered[-validation:], 'feature_columns': eligible,
                 'target_column': spec['target_column'], 'evaluation_executed': False,
                 'training_table': subset(ordered[:-validation]), 'validation_table': subset(ordered[-validation:])}
    return {'kind': 'forecast_suitability_review', 'status': 'insufficient_data' if split is None else 'review_required' if issues else 'validation_plan_only',
            'frequency': frequency, 'horizon_periods': horizon, 'observed_rows': len(periods), 'unique_periods': len(unique),
            'ordered_row_indices': ordered, 'missing_periods': missing, 'issues': issues, 'split': split,
            'eligible_features': eligible, 'excluded_features': excluded, 'unresolved_features': unresolved,
            'fitted_model': False, 'accuracy': 'not_established',
            'policy': 'Conservative diagnostics: training periods >= max(2,horizon); declared holdout; two cycles for a declared season. These are not sufficiency or accuracy guarantees.',
            'availability_scope': 'Supplied row-level availability only; unknown provenance, hidden leakage, and feature engineering are not independently audited. Date-only values have day precision.'}


def forecast_display(table, spec):
    """Validate caller-supplied observations/scenarios and preserve every cell."""
    forecast = spec['forecast']
    require(not table['formulas'], 'Forecast chart inputs require explicit values')
    observed, predicted = _column(table, forecast['observed']), _column(table, forecast['predicted'])
    source, category = _column(table, forecast['source_column']), _column(table, spec['category'])
    periods = [_period(row[category], forecast['frequency']) for row in table['rows']]
    require(periods == sorted(set(periods)), 'Forecast chart periods must be unique and chronological')
    require(all(b-a == 1 for a, b in zip(periods, periods[1:])), 'Forecast charts require contiguous periods; resolve missing periods explicitly')
    observed_rows, forecast_rows, provenance = [], [], []
    bounded = 'lower' in forecast
    lower, upper = (_column(table, forecast['lower']), _column(table, forecast['upper'])) if bounded else (None, None)
    for i, row in enumerate(table['rows']):
        has_observed, has_predicted = number(row[observed]) is not None, number(row[predicted]) is not None
        require(has_observed != has_predicted and (row[predicted] is None if has_observed else row[observed] is None), 'Each forecast chart row requires exactly one numeric observed or predicted value')
        require(bool(text(row[source], 500, empty=False).strip()), 'Forecast source annotation cannot be blank')
        (observed_rows if has_observed else forecast_rows).append(i)
        provenance.append({'row_index': i, 'period': row[category], 'kind': 'observed' if has_observed else 'forecast', 'source': row[source]})
        if bounded:
            if has_observed: require(row[lower] is None and row[upper] is None, 'Forecast bounds cannot label observations')
            else: require(number(row[lower]) is not None and number(row[upper]) is not None and number(row[lower]) <= number(row[predicted]) <= number(row[upper]), 'Supplied bounds must contain the predicted value')
    require(observed_rows and forecast_rows and max(observed_rows) < min(forecast_rows), 'Forecast periods must follow the observed periods')
    return {'observed_rows': observed_rows, 'forecast_rows': forecast_rows, 'provenance': provenance,
            'basis': forecast['basis'], 'fitted_model': False, 'accuracy': 'not_established',
            'uncertainty': 'supplied_bounds_not_calibrated' if bounded else 'not_provided',
            **({'interval_basis': forecast['interval_basis']} if bounded else {}),
            'source_scope': 'Caller-supplied annotations, not an independent verification of the external source or forecast.'}


def statistics(values, *, ddof=1, quantiles=(.25, .5, .75), weights=None):
    require(ddof in (0, 1), 'ddof must be 0 or 1')
    nums = sorted(number(v) for v in values if number(v) is not None)
    count, n = len(values), len(nums)
    with localcontext() as ctx:
        ctx.prec = 34
        total = sum(nums, Decimal(0)) if nums else None
        mean = total / n if n else None
        median = nums[n//2] if n % 2 else (nums[n//2-1] + nums[n//2]) / 2 if n else None
        variance = sum((v - mean) ** 2 for v in nums) / (n - ddof) if n > ddof else None
        qs = {}
        for q in quantiles:
            require(type(q) in (int, float) and math.isfinite(q) and 0 <= q <= 1, 'Invalid quantile')
            h = Decimal(str(q)) * (n - 1)
            low = int(h) if n else 0
            value = nums[low] + (nums[min(low+1, n-1)]-nums[low]) * (h-low) if n else None
            qs[str(q)] = decstr(value)
        result = {'count': count, 'numeric_count': n, 'missing': sum(v is None for v in values), 'nonnumeric': sum(v is not None and number(v) is None for v in values), 'sum': decstr(total), 'mean': decstr(mean), 'median': decstr(median), 'min': decstr(min(nums)) if n else None, 'max': decstr(max(nums)) if n else None, 'variance': decstr(variance), 'stddev': decstr(variance.sqrt()) if variance is not None else None, 'ddof': ddof, 'quantile_method': 'linear interpolation (R7), h=(n-1)*q', 'quantiles': qs, 'precision': 'Decimal 34 significant digits; no intermediate binary float arithmetic'}
        result['nonnumeric_row_indices'] = [i for i, v in enumerate(values) if v is not None and number(v) is None]
        if weights is not None:
            require(len(weights) == count, 'Weights must align with rows')
            pairs = []
            excluded = []
            for index, (value, weight) in enumerate(zip(values, weights)):
                v, w = number(value), number(weight)
                if w is not None:
                    require(w >= 0, 'Negative weights are not supported')
                if v is None or w is None:
                    excluded.append(index)
                else:
                    pairs.append((v, w))
            denominator = sum((w for _, w in pairs), Decimal(0))
            result.update({'weighted_mean': decstr(sum((v*w for v, w in pairs), Decimal(0))/denominator) if denominator else None, 'weight_sum': decstr(denominator), 'weighted_excluded_row_indices': excluded})
    return result


def _statistics_warnings(result, column):
    messages = []
    if result['nonnumeric']:
        messages.append(f"Column {column}: {result['nonnumeric']} nonnumeric values excluded; numeric results cover {result['numeric_count']} of {result['count']} rows. Explicit cleaning is required to interpret numeric text.")
    excluded = result.get('weighted_excluded_row_indices', [])
    if excluded:
        messages.append(f'Column {column}: weighted mean excludes {len(excluded)} rows with missing or nonnumeric values/weights; see row indices.')
    return messages


def clean_table(table, rules):
    table = validate_table(table)
    before = profile(table)
    changes, warnings = [], []
    for rule in rules:
        operation = rule['operation']
        if operation == 'deduplicate':
            require(not table['formulas'], 'Deduplication with formulas requires a reference-aware engine')
            indices = [_column(table, name) for name in rule['columns']]
            seen, retained, removed = set(), [], []
            for index, row in enumerate(table['rows']):
                key = _key([row[i] for i in indices])
                if key in seen:
                    removed.append(index)
                else:
                    seen.add(key); retained.append(row)
            table['rows'] = retained
            changes.append({'operation': operation, 'removed_row_indices': removed})
            continue
        index = _column(table, rule['column'])
        if operation == 'rename':
            text(rule['value'], 100, empty=False)
            require(rule['value'] not in table['columns'] or rule['value'] == rule['column'], 'Rename would create duplicate header')
            table['columns'][index] = rule['value']
            changes.append({'operation': operation, 'column': rule['column'], 'new_name': rule['value']})
            continue
        changed = []
        for r, row in enumerate(table['rows'], 2):
            value, location = row[index], addr(r, index)
            if location in table['formulas']:
                warnings.append('Formula preserved without cleaning at ' + location)
                continue
            replacement = value
            try:
                if operation == 'trim' and type(value) is str:
                    replacement = value.strip()
                elif operation == 'fill_missing' and value is None:
                    replacement = rule['value']
                elif operation == 'empty_to_null' and value == '':
                    replacement = None
                elif operation == 'parse_number' and type(value) is str:
                    # Only the explicitly selected quantitative column changes.
                    # Identifiers and other raw CSV cells retain their script.
                    normalized = value.translate(NUMERIC_DIGITS)
                    decimal = rule.get('decimal_separator', '.')
                    thousands = rule.get('thousands_separator', '')
                    pattern = r'[+-]?(?:[0-9]{1,3}(?:' + re.escape(thousands) + r'[0-9]{3})+|[0-9]+)' if thousands else r'[+-]?[0-9]+'
                    pattern += r'(?:' + re.escape(decimal) + r'[0-9]+)?'
                    require(bool(re.fullmatch(pattern, normalized)), 'Number does not match explicit separators')
                    numeric = Decimal((normalized.replace(thousands, '') if thousands else normalized).replace(decimal, '.'))
                    replacement = _native_number(numeric)
                elif operation == 'parse_date' and type(value) is str:
                    parsed = datetime.strptime(value, rule['date_format']).date()
                    require(parsed.strftime(rule['date_format']) == value, 'Date must match exact explicit format')
                    replacement = parsed.isoformat()
            except (ValueError, InvalidOperation) as exc:
                if rule.get('on_error', 'reject') == 'reject':
                    raise ValueError('Cleaning failed at ' + location + ': ' + str(exc)) from exc
                warnings.append('Unchanged invalid input at ' + location)
                replacement = value
            if type(value) != type(replacement) or value != replacement:
                row[index] = replacement; changed.append(location)
        changes.append({'operation': operation, 'column': rule['column'], 'changed_cells': changed})
    return table, {'before': before, 'after': profile(table), 'changes': changes, 'rules': rules}, warnings


def join_tables(left, right, spec):
    left, right = validate_table(left), validate_table(right)
    require(not left['formulas'] and not right['formulas'], 'Join unresolved formulas only after an explicit recalculation/materialization step')
    li = [_column(left, v) for v in spec['left_keys']]
    ri = [_column(right, v) for v in spec['right_keys']]
    for l, r in zip(li, ri):
        lt = {type(row[l]).__name__ for row in left['rows'] if row[l] is not None}
        rt = {type(row[r]).__name__ for row in right['rows'] if row[r] is not None}
        require(len(lt | rt) <= 1, 'Join key types differ; no implicit normalization')
    def key(row, indices, side, index):
        vals = [row[i] for i in indices]
        return ('null', side, index) if None in vals and not spec.get('nulls_equal', False) else _key(vals)
    lk = [key(row, li, 'left', i) for i, row in enumerate(left['rows'])]
    rk = [key(row, ri, 'right', i) for i, row in enumerate(right['rows'])]
    lc, rc = Counter(lk), Counter(rk)
    require(spec['cardinality'] not in ('1:1', '1:m') or max(lc.values(), default=0) <= 1, 'Left duplicate keys violate declared cardinality')
    require(spec['cardinality'] not in ('1:1', 'm:1') or max(rc.values(), default=0) <= 1, 'Right duplicate keys violate declared cardinality')
    predicted = sum(lc[k] * rc.get(k, 0) if k in rc else lc[k] if spec['how'] in ('left', 'full') else 0 for k in lc)
    predicted += sum(rc[k] for k in rc if k not in lc) if spec['how'] == 'full' else 0
    require(predicted <= spec.get('max_output_rows', 1000), 'Join row multiplication exceeds approved bound')
    columns = list(left['columns'])
    right_names = []
    for name in right['columns']:
        candidate = name + '_right' if name in columns else name
        require(candidate not in columns, 'Join output column conflict; rename explicitly')
        columns.append(candidate); right_names.append(candidate)
    require(len(columns) <= MAX_COLS and len(columns)*predicted <= MAX_CELLS, 'Join output exceeds cell bounds')
    groups = defaultdict(list)
    for i, k in enumerate(rk):
        groups[k].append(i)
    rows, lineage = [], []
    for i, k in enumerate(lk):
        matches = groups[k]
        for j in matches:
            rows.append(left['rows'][i] + right['rows'][j]); lineage.append([i, j])
        if not matches and spec['how'] in ('left', 'full'):
            rows.append(left['rows'][i] + [None]*len(right['columns'])); lineage.append([i, None])
    if spec['how'] == 'full':
        for j, k in enumerate(rk):
            if k not in lc:
                rows.append([None]*len(left['columns']) + right['rows'][j]); lineage.append([None, j])
    return {'columns': columns, 'rows': rows, 'formulas': {}}, {'predicted_rows': predicted, 'actual_rows': len(rows), 'unmatched_left': sum(k not in rc for k in lk), 'unmatched_right': sum(k not in lc for k in rk), 'left_duplicate_key_rows': sum(v for v in lc.values() if v > 1), 'right_duplicate_key_rows': sum(v for v in rc.values() if v > 1), 'lineage_row_indices': lineage, 'cardinality': spec['cardinality'], 'nulls_equal': spec.get('nulls_equal', False)}


def aggregate_table(table, spec):
    table = validate_table(table)
    require(not table['formulas'], 'Unresolved formula cells cannot be aggregated as stored values')
    groups = [_column(table, v) for v in spec['group_by']]
    require(spec['operation'] not in spec['group_by'], 'Aggregate output name conflicts with a grouping column')
    value_index = _column(table, spec['value_column']) if 'value_column' in spec else None
    weight_index = _column(table, spec['weight_column']) if 'weight_column' in spec else None
    unit_index = _column(table, spec['unit_column']) if 'unit_column' in spec else None
    grouped = defaultdict(list)
    for row in table['rows']:
        grouped[_key([row[i] for i in groups])].append(row)
    output, details = [], []
    for group, rows in grouped.items():
        if unit_index is not None:
            require(len({_key([row[unit_index]]) for row in rows}) <= 1, 'Mixed units/currencies require separate grouping')
        values = [row[value_index] for row in rows] if value_index is not None else []
        stats = statistics(values, weights=[row[weight_index] for row in rows] if weight_index is not None else None)
        operation = spec['operation']
        result = len(rows) if operation == 'count' else len({_key([v]) for v in values if v is not None}) if operation == 'distinct' else stats.get(operation)
        # Aggregates are JSON decimal strings in summary and typed numeric cells
        # only when the XLSX-safe 15-digit conversion is exact enough to export.
        output.append([item[1] for item in group] + [_typed_measure(result)])
        details.append({'group': [item[1] for item in group], 'rows': len(rows), 'statistics': stats})
    return {'columns': spec['group_by'] + [spec['operation']], 'rows': output, 'formulas': {}}, {'kind': 'ordinary grouped aggregation, not an Excel PivotTable', 'record_unit': spec['record_unit'], 'groups': details, 'input_rows': len(table['rows']), 'output_rows': len(output), 'precision_policy': 'Exact Decimal summary strings; output values use numeric cells only within 15 significant digits and 1e14, otherwise exact text is retained'}


def _archive(content):
    from .document_extract import Archive, Limits
    require(type(content) is bytes and len(content) <= MAX_BYTES, 'Source must be bytes within 8 MiB')
    return Archive(content, Limits(max_cells=MAX_CELLS))


def read_xlsx(content, request):
    from .document_extract import office_pages, xlsx_page, Limits
    archive = _archive(content)
    try:
        pages = office_pages('xlsx', archive)
        require(0 < len(pages) <= 200 and request['sheet_index'] < len(pages), 'Selected worksheet does not exist')
        workbook = archive.tree('xl/workbook.xml')
        sheet_nodes = workbook.findall('s:sheets/s:sheet', NS)
        title, part = pages[request['sheet_index']]
        page = xlsx_page(archive, title, part, request['sheet_index'] + 1, Limits(max_cells=MAX_CELLS))
        tree = archive.tree(part)
        cells = page['elements'][0]['cells']
        require(all(c['row'] <= MAX_ROWS + 1 and c['column'] <= MAX_COLS for c in cells), 'Worksheet occupied range exceeds 1001 rows/26 columns')
        relationships = []
        for name in archive.names:
            if name.endswith('.rels'):
                for relation in archive.tree(name):
                    if relation.get('TargetMode') == 'External':
                        relationships.append({'part': name, 'type': relation.get('Type', '').rsplit('/', 1)[-1], 'target_present': True})
        summary = {'kind': 'xlsx', 'sheet': title, 'sheet_index': request['sheet_index'], 'sheet_part': part, 'sheets': [{'name': node.get('name'), 'state': node.get('state', 'visible')} for node in sheet_nodes], 'defined_names': [{'name': n.get('name'), 'formula': (n.text or ''), 'hidden': n.get('hidden') == '1'} for n in workbook.findall('s:definedNames/s:definedName', NS)], 'merged_ranges': page['elements'][0].get('merged_ranges', []), 'autofilter': tree.find('s:autoFilter', NS).attrib if tree.find('s:autoFilter', NS) is not None else None, 'hidden_rows': [node.get('r') for node in tree.findall('s:sheetData/s:row', NS) if node.get('hidden') == '1'], 'hidden_columns': [dict(node.attrib) for node in tree.findall('s:cols/s:col', NS) if node.get('hidden') == '1'], 'table_parts': [p for p in sorted(archive.names) if p.startswith('xl/tables/') and p.endswith('.xml')], 'external_relationships': relationships, 'cells': cells, 'cache_status': 'stored cache may be missing or stale; never certified fresh by reading', 'formulas_executed': False}
        max_row = max((c['row'] for c in cells), default=request['header_row'])
        max_col = max((c['column'] for c in cells), default=0)
        matrix = [[None] * max_col for _ in range(max_row)]
        formulas = {}
        for cell in cells:
            value = cell['value']
            if cell.get('formula') is not None:
                formulas[cell['address']] = '=' + cell['formula']
                value = None
            elif cell['kind'] == 'number':
                try:
                    value = _native_number(Decimal(value))
                except (ValueError, InvalidOperation, OverflowError):
                    value = cell['value']
                    summary.setdefault('warnings', []).append('Numeric value retained as raw text at ' + cell['address'])
            elif cell['kind'] == 'boolean':
                require(value in ('0', '1'), 'Invalid XLSX boolean')
                value = value == '1'
            elif cell['kind'] == 'blank':
                value = None
            matrix[cell['row']-1][cell['column']-1] = value
        headers = matrix[request['header_row'] - 1] if matrix else []
        issues = _header_issues(headers)
        summary['header_issues'] = issues
        if issues:
            return None, summary
        # Non-first header rows cannot reuse A1 formula references in a new table.
        require(not formulas or request['header_row'] == 1, 'Formula-bearing tables currently require header_row=1')
        table = {'columns': headers, 'rows': matrix[request['header_row']:], 'formulas': formulas}
        return validate_table(table), summary
    finally:
        archive.zip.close()


def _header_issues(headers):
    issues = []
    if not headers:
        issues.append({'kind': 'missing_headers'})
    for index, name in enumerate(headers):
        if type(name) is not str or not name.strip():
            issues.append({'kind': 'empty_or_nontext_header', 'column_index': index})
        elif len(name) > 100:
            issues.append({'kind': 'header_too_long', 'column_index': index})
    duplicates = {name for name in headers if type(name) is str and headers.count(name) > 1}
    if duplicates:
        issues.append({'kind': 'duplicate_headers', 'names': sorted(duplicates)})
    return issues


def read_csv(content, request):
    require(type(content) is bytes and len(content) <= MAX_BYTES, 'CSV exceeds 8 MiB')
    require(b'\0' not in content, 'CSV contains NUL')
    try:
        decoded = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('CSV requires UTF-8; choose an explicit conversion outside this operation') from exc
    records = []
    try:
        for row in csv.reader(StringIO(decoded, newline=''), delimiter=request['delimiter'], strict=True):
            require(len(records) < MAX_ROWS + 1 and len(row) <= MAX_COLS, 'CSV exceeds row/column bound')
            for value in row:
                text(value)
            records.append(row)
    except csv.Error as exc:
        raise ValueError('Malformed CSV quoting') from exc
    headers = records[0] if records else []
    issues = _header_issues(headers)
    if records:
        require(all(len(row) == len(headers) for row in records[1:]), 'Ragged CSV rows; no implicit padding or dropping')
    summary = {'kind': 'csv', 'encoding': 'utf-8-sig' if content.startswith(b'\xef\xbb\xbf') else 'utf-8', 'delimiter': request['delimiter'], 'records': max(0, len(records)-1), 'header_issues': issues, 'type_inference': False, 'missing_policy': 'CSV empty fields remain empty text; null is not inferred', 'formula_execution': False}
    if issues:
        return None, summary
    return validate_table({'columns': headers, 'rows': records[1:]}), summary


def export_csv(table, request):
    require(not table['formulas'], 'CSV cannot preserve native formulas; materialize explicitly first')
    stream = StringIO(newline='')
    writer = csv.writer(stream, delimiter=request['delimiter'], lineterminator='\r\n', quoting=csv.QUOTE_MINIMAL)
    escaped, nulls = [], []
    for row_index, row in enumerate([table['columns']] + table['rows'], 1):
        output = []
        for col, value in enumerate(row):
            if value is None:
                nulls.append(addr(row_index, col)); value = ''
            elif type(value) is bool:
                value = 'true' if value else 'false'
            if type(value) is str and re.match(r'^[\s\ufeff]*[=+@-]', value) and request['formula_safe']:
                value = "'" + value; escaped.append(addr(row_index, col))
            output.append(value)
        writer.writerow(output)
    content = stream.getvalue().encode('utf-8-sig')
    return DataArtifact(content, 'riwaq-data.csv', 'text/csv; charset=utf-8', hashlib.sha256(content).hexdigest(), {'encoding': 'utf-8-sig', 'delimiter': request['delimiter'], 'rows': len(table['rows']), 'formula_safe': request['formula_safe'], 'prefixed_text_cells': escaped, 'null_exported_as_empty_cells': nulls}, ('CSV has no native cell types, null marker, formulas or formats; null and empty text both serialize as empty fields.', 'Spreadsheet-safe mode prefixes risky text with an apostrophe and reports every changed coordinate; raw mode preserves text and may be executable in spreadsheet apps.'))


class FormulaError(ValueError):
    pass


class _Formula:
    """Small recursive-descent grammar; no eval or dynamic function lookup."""
    TOKEN = re.compile(r'\s*(\$?[A-Z]\$?[1-9][0-9]{0,3}|[A-Z]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)|[+\-*/():,])')

    def __init__(self, formula, resolve):
        expression = (formula[1:] if formula.startswith('=') else formula).strip()
        self.tokens, offset = [], 0
        while offset < len(expression):
            match = self.TOKEN.match(expression, offset)
            if not match:
                raise FormulaError('unsupported_formula_syntax')
            self.tokens.append(match[1]); offset = match.end()
            if len(self.tokens) > 256:
                raise FormulaError('formula_token_limit')
        self.i, self.resolve, self.depth = 0, resolve, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, token=None):
        actual = self.peek()
        if actual is None or token is not None and actual != token:
            raise FormulaError('malformed_formula')
        self.i += 1
        return actual

    @staticmethod
    def numeric(value):
        if value is None:
            return Decimal(0)
        if not isinstance(value, Decimal):
            raise FormulaError('nonnumeric_operand')
        return value

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 40:
            raise FormulaError('formula_depth_limit')
        token = self.take()
        if token in ('+', '-'):
            value = self.numeric(self.expression(3)) * (1 if token == '+' else -1)
        elif token == '(':
            value = self.expression(); self.take(')')
        elif re.fullmatch(r'\$?[A-Z]\$?[1-9][0-9]{0,3}', token):
            start = address(token)
            if self.peek() == ':':
                self.take(':'); end = address(self.take())
                x1, y1, x2, y2 = ord(start[0])-65, int(start[1:]), ord(end[0])-65, int(end[1:])
                if x2 < x1 or y2 < y1 or (x2-x1+1)*(y2-y1+1) > MAX_CELLS:
                    raise FormulaError('invalid_range')
                value = [self.resolve(addr(r, c)) for r in range(y1, y2+1) for c in range(x1, x2+1)]
            else:
                value = self.resolve(start)
        elif re.fullmatch(r'[A-Z]+', token):
            if token not in ('SUM', 'AVERAGE', 'COUNT', 'MIN', 'MAX', 'MEDIAN'):
                raise FormulaError('unsupported_function:' + token)
            self.take('('); args = []
            if self.peek() != ')':
                while True:
                    item = self.expression(); args.extend(item if type(item) is list else [item])
                    if self.peek() != ',':
                        break
                    self.take(',')
            self.take(')')
            nums = sorted(v for v in args if isinstance(v, Decimal))
            if token == 'SUM': value = sum(nums, Decimal(0))
            elif token == 'COUNT': value = Decimal(len(nums))
            elif not nums: raise FormulaError('no_numeric_values')
            elif token == 'AVERAGE': value = sum(nums, Decimal(0))/len(nums)
            elif token == 'MIN': value = min(nums)
            elif token == 'MAX': value = max(nums)
            else:
                n = len(nums); value = nums[n//2] if n % 2 else (nums[n//2-1]+nums[n//2])/2
        else:
            try: value = Decimal(token)
            except InvalidOperation as exc: raise FormulaError('invalid_number') from exc
        while self.peek() in ('+', '-', '*', '/'):
            op = self.peek(); priority = 1 if op in ('+', '-') else 2
            if priority < minimum:
                break
            self.take(); right = self.numeric(self.expression(priority+1)); left = self.numeric(value)
            if op == '+': value = left+right
            elif op == '-': value = left-right
            elif op == '*': value = left*right
            elif right == 0: raise FormulaError('division_by_zero')
            else: value = left/right
            decstr(value)
        self.depth -= 1
        return value

    def calculate(self):
        with localcontext() as ctx:
            ctx.prec = 34
            value = self.expression()
            if self.peek() is not None or not isinstance(value, Decimal):
                raise FormulaError('not_a_scalar_formula')
            decstr(value)
            return value


def recalculate(table):
    table = validate_table(table)
    values = {addr(r, c): value for r, row in enumerate([table['columns']]+table['rows'], 1) for c, value in enumerate(row)}
    calculated, errors, pending = {}, {}, set()
    def resolve(location):
        if location not in values:
            raise FormulaError('reference_outside_bounded_table:' + location)
        if location in pending:
            raise FormulaError('circular_reference:' + location)
        if location in calculated:
            return calculated[location]
        if location not in table['formulas']:
            return number(values[location]) if number(values[location]) is not None else values[location]
        if len(pending) >= 40:
            raise FormulaError('formula_dependency_depth_limit')
        pending.add(location)
        try:
            value = _Formula(table['formulas'][location], resolve).calculate()
            calculated[location] = value
            return value
        finally:
            pending.remove(location)
    for location in table['formulas']:
        try:
            resolve(location)
        except (FormulaError, ValueError, InvalidOperation) as exc:
            errors[location] = str(exc)
    return {'engine': VERSION + ' bounded Decimal formula grammar', 'supported': ['arithmetic + - * / parentheses', 'same-sheet A1 and $A$1 references', 'bounded ranges', 'SUM', 'AVERAGE', 'COUNT', 'MIN', 'MAX', 'MEDIAN'], 'calculated_values': {key: decstr(value) for key, value in calculated.items()}, 'errors': errors, 'all_supported_formulas_calculated': not errors, 'native_excel_recalculation_observed': False, 'logical_formula_correctness': 'Requires explicit expected outcomes; successful evaluation does not prove the business formula is correct'}


def edit_formulas(table, edits):
    table = validate_table(table)
    original = recalculate(table)
    changed = []
    for edit in edits:
        location = address(edit['cell'])
        row, col = int(location[1:])-2, ord(location[0])-65
        require(0 <= row < len(table['rows']) and col < len(table['columns']), 'Edits target existing data cells only')
        old = table['formulas'].get(location, table['rows'][row][col])
        if 'expected' in edit:
            require(type(old) == type(edit['expected']) and old == edit['expected'], 'Cell changed since expected value; patch rejected')
        if 'formula' in edit:
            table['formulas'][location] = edit['formula']; table['rows'][row][col] = None
        else:
            table['formulas'].pop(location, None); table['rows'][row][col] = edit['value']
        changed.append(location)
    after = recalculate(table)
    return table, {'before': original, 'after': after, 'changed_cells': changed, 'recalculated_scope': 'all formulas and dependent formulas in the bounded selected worksheet; unrelated package parts preserved'}


def _node(parent, namespace, name, attrs=None, value=None):
    child = ET.SubElement(parent, '{'+namespace+'}'+name, attrs or {})
    child.text = value
    return child


def _xml(root):
    raw = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    namespace = root.tag.split('}')[0][1:]
    pattern = rb'xmlns:([A-Za-z0-9_]+)="' + re.escape(namespace.encode()) + rb'"'
    match = re.search(pattern, raw)
    if match:
        prefix = match[1]
        raw = raw.replace(match[0], b'xmlns="'+namespace.encode()+b'"').replace(b'<'+prefix+b':', b'<').replace(b'</'+prefix+b':', b'</')
    return raw


def _set_cell(cell, value, formula=None, cache=None):
    for child in list(cell):
        if child.tag in ('{'+S+'}v', '{'+S+'}f', '{'+S+'}is'):
            cell.remove(child)
    cell.attrib.pop('t', None)
    if formula is not None:
        _node(cell, S, 'f', value=formula.removeprefix('='))
        if cache is not None:
            _node(cell, S, 'v', value=cache)
    elif value is None:
        pass
    elif type(value) is str:
        cell.set('t', 'inlineStr'); inline = _node(cell, S, 'is')
        _node(inline, S, 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}, value)
    elif type(value) is bool:
        cell.set('t', 'b'); _node(cell, S, 'v', value='1' if value else '0')
    else:
        _node(cell, S, 'v', value=str(value))


def _bar_axis_scaling(scaling, values):
    require(values, 'Chart requires at least one numeric value')
    for old in list(scaling):
        if old.tag in ('{' + C + '}min', '{' + C + '}max'):
            scaling.remove(old)
    # A zero-only series still needs a non-degenerate axis. Limits describe
    # display bounds, never an invented data point in the series cache.
    if max(values) <= 0:
        _node(scaling, C, 'max', {'val': '0' if min(values) < 0 else '1'})
    _node(scaling, C, 'min', {'val': decstr(min(Decimal(0), min(values)))})


def _chart_parts(parts, sheet_part, sheet_name, sheet, table, spec):
    require(len(table['rows']) <= 200 and table['rows'], 'Charts support 1–200 bounded data rows')
    category = _column(table, spec['category'])
    series_indices = [_column(table, name) for name in spec['values']]
    require(len(set(series_indices)) == len(series_indices), 'Duplicate chart series')
    require(not table['formulas'], 'Chart formula series require explicit materialization first')
    for index in series_indices:
        require(all(row[index] is None or number(row[index]) is not None for row in table['rows']), 'Chart series require numeric values or null')
    require(sheet.find('s:drawing', NS) is None, 'Adding to a worksheet with an existing drawing is not supported')
    chart = ET.Element('{'+C+'}chartSpace')
    _node(chart, C, 'lang', {'val': 'ar-SA'})
    chart_body = _node(chart, C, 'chart')
    title = _node(chart_body, C, 'title'); rich = _node(_node(title, C, 'tx'), C, 'rich')
    _node(rich, A, 'bodyPr'); _node(rich, A, 'lstStyle')
    paragraph = _node(rich, A, 'p'); run = _node(paragraph, A, 'r')
    _node(run, A, 'rPr', {'lang': 'ar-SA', 'sz': '1400'}); _node(run, A, 't', value=spec['title'] + ' (' + spec['unit'] + ')')
    _node(title, C, 'overlay', {'val': '0'})
    plot = _node(chart_body, C, 'plotArea'); _node(plot, C, 'layout')
    plot_type = _node(plot, C, 'barChart' if spec['kind'] == 'bar' else 'lineChart')
    if spec['kind'] == 'bar':
        _node(plot_type, C, 'barDir', {'val': 'col'})
    _node(plot_type, C, 'grouping', {'val': 'clustered' if spec['kind'] == 'bar' else 'standard'})
    quoted_sheet = "'" + sheet_name.replace("'", "''") + "'!"
    for sequence, index in enumerate(series_indices):
        series = _node(plot_type, C, 'ser')
        _node(series, C, 'idx', {'val': str(sequence)}); _node(series, C, 'order', {'val': str(sequence)})
        if 'forecast' in spec:
            # The role is explicit in the contract; do not infer it from a
            # name or present a future scenario as a measured observation.
            _node(_node(series, C, 'tx'), C, 'v', value=FORECAST_LABELS[sequence])
            line = _node(_node(series, C, 'spPr'), A, 'ln', {'w': '28575'})
            _node(_node(line, A, 'solidFill'), A, 'srgbClr', {'val': ['0F6B5E', 'B86918', '697786', '697786'][sequence]})
            _node(line, A, 'prstDash', {'val': ['solid', 'dash', 'sysDot', 'sysDot'][sequence]})
        else:
            label = _node(_node(series, C, 'tx'), C, 'strRef')
            _node(label, C, 'f', value=quoted_sheet + '$' + chr(65+index) + '$1')
            label_cache = _node(label, C, 'strCache'); _node(label_cache, C, 'ptCount', {'val': '1'})
            _node(_node(label_cache, C, 'pt', {'idx': '0'}), C, 'v', value=table['columns'][index])
            # Our minimal workbook intentionally has no theme part. Relying on
            # the reader's theme defaults produced invisible bars in Calc.
            color = ['2563EB', '0F766E', 'B45309', '7C3AED', 'BE123C', '334155'][sequence % 6]
            shape = _node(series, C, 'spPr')
            if spec['kind'] == 'bar':
                _node(_node(shape, A, 'solidFill'), A, 'srgbClr', {'val': color})
            line = _node(shape, A, 'ln', {'w': '28575'})
            _node(_node(line, A, 'solidFill'), A, 'srgbClr', {'val': color})
        category_ref = _node(_node(series, C, 'cat'), C, 'strRef')
        _node(category_ref, C, 'f', value=quoted_sheet + f'${chr(65+category)}$2:${chr(65+category)}${len(table["rows"])+1}')
        category_cache = _node(category_ref, C, 'strCache'); _node(category_cache, C, 'ptCount', {'val': str(len(table['rows']))})
        value_ref = _node(_node(series, C, 'val'), C, 'numRef')
        _node(value_ref, C, 'f', value=quoted_sheet + f'${chr(65+index)}$2:${chr(65+index)}${len(table["rows"])+1}')
        value_cache = _node(value_ref, C, 'numCache'); _node(value_cache, C, 'formatCode', value='General'); _node(value_cache, C, 'ptCount', {'val': str(len(table['rows']))})
        for i, row in enumerate(table['rows']):
            _node(_node(category_cache, C, 'pt', {'idx': str(i)}), C, 'v', value='' if row[category] is None else str(row[category]))
            if row[index] is not None:
                _node(_node(value_cache, C, 'pt', {'idx': str(i)}), C, 'v', value=str(row[index]))
        if 'forecast' in spec: _node(series, C, 'smooth', {'val': '0'})
    if 'forecast' in spec: _node(plot_type, C, 'smooth', {'val': '0'})
    for axis in ('10001', '10002'):
        _node(plot_type, C, 'axId', {'val': axis})
    for kind, axis, other, position in [('catAx', '10001', '10002', 'b'), ('valAx', '10002', '10001', 'l')]:
        node = _node(plot, C, kind); _node(node, C, 'axId', {'val': axis})
        scaling = _node(node, C, 'scaling')
        _node(scaling, C, 'orientation', {'val': 'minMax'})
        if kind == 'valAx' and spec['kind'] == 'bar':
            values = [number(row[i]) for row in table['rows'] for i in series_indices if row[i] is not None]
            # Bar lengths need a visible zero baseline. Do not clip negatives.
            _bar_axis_scaling(scaling, values)
        _node(node, C, 'delete', {'val': '0'}); _node(node, C, 'axPos', {'val': position})
        if kind == 'valAx':
            _node(node, C, 'majorGridlines'); _node(node, C, 'numFmt', {'formatCode': 'General', 'sourceLinked': '1'})
        _node(node, C, 'tickLblPos', {'val': 'nextTo'}); _node(node, C, 'crossAx', {'val': other}); _node(node, C, 'crosses', {'val': 'autoZero'})
        if kind == 'catAx':
            _node(node, C, 'auto', {'val': '1'}); _node(node, C, 'lblAlgn', {'val': 'ctr'}); _node(node, C, 'lblOffset', {'val': '100'})
        else:
            _node(node, C, 'crossBetween', {'val': 'between'})
            if spec.get('display_unit', 'units') != 'units':
                _node(_node(node, C, 'dispUnits'), C, 'builtInUnit', {'val': spec['display_unit']})
    if 'forecast' in spec:
        legend = _node(chart_body, C, 'legend')
        _node(legend, C, 'legendPos', {'val': 'b'}); _node(legend, C, 'layout'); _node(legend, C, 'overlay', {'val': '0'})
    _node(chart_body, C, 'plotVisOnly', {'val': '1'}); _node(chart_body, C, 'dispBlanksAs', {'val': 'gap'})
    drawing = ET.Element('{'+XDR+'}wsDr')
    anchor = _node(drawing, XDR, 'twoCellAnchor')
    # Keep the chart below the source table, inside a single printed page width.
    # The previous side-by-side anchor split the graphic/title across RTL pages.
    top = len(table['rows']) + 3
    for name, col, row in [('from', 0, top), ('to', 8, top+18)]:
        marker = _node(anchor, XDR, name)
        for tag, val in [('col', col), ('colOff', 0), ('row', row), ('rowOff', 0)]:
            _node(marker, XDR, tag, value=str(val))
    frame = _node(anchor, XDR, 'graphicFrame', {'macro': ''})
    nv = _node(frame, XDR, 'nvGraphicFramePr'); _node(nv, XDR, 'cNvPr', {'id': '2', 'name': spec['title']}); _node(nv, XDR, 'cNvGraphicFramePr')
    transform = _node(frame, XDR, 'xfrm'); _node(transform, A, 'off', {'x': '0', 'y': '0'}); _node(transform, A, 'ext', {'cx': '0', 'cy': '0'})
    data = _node(_node(frame, A, 'graphic'), A, 'graphicData', {'uri': C}); _node(data, C, 'chart', {'{'+R+'}id': 'rIdChart'})
    _node(anchor, XDR, 'clientData')
    # Generated trusted templates have no drawings. Never replace an existing part.
    for part in ('xl/charts/chart1.xml', 'xl/drawings/drawing1.xml'):
        require(part not in parts, 'Existing chart/drawing collision')
    parts['xl/charts/chart1.xml'] = _xml(chart)
    parts['xl/drawings/drawing1.xml'] = _xml(drawing)
    rels = ET.Element('{'+REL+'}Relationships')
    _node(rels, REL, 'Relationship', {'Id': 'rIdChart', 'Type': R+'/chart', 'Target': '../charts/chart1.xml'})
    parts['xl/drawings/_rels/drawing1.xml.rels'] = _xml(rels)
    sheet_rel = sheet_part.rsplit('/', 1)[0] + '/_rels/' + sheet_part.rsplit('/', 1)[1] + '.rels'
    rels = ET.fromstring(parts[sheet_rel]) if sheet_rel in parts else ET.Element('{'+REL+'}Relationships')
    require(not any(n.get('Id') == 'rIdDataChart' for n in rels), 'Drawing relationship collision')
    _node(rels, REL, 'Relationship', {'Id': 'rIdDataChart', 'Type': R+'/drawing', 'Target': '../drawings/drawing1.xml'})
    parts[sheet_rel] = _xml(rels)
    properties = sheet.find('s:sheetPr', NS)
    if properties is None:
        properties = ET.Element('{'+S+'}sheetPr'); sheet.insert(0, properties)
    setup_properties = properties.find('s:pageSetUpPr', NS)
    if setup_properties is None: setup_properties = _node(properties, S, 'pageSetUpPr')
    setup_properties.set('fitToPage', '1')
    for tag in ('pageMargins', 'pageSetup'):
        for previous in sheet.findall('s:'+tag, NS): sheet.remove(previous)
    _node(sheet, S, 'pageMargins', {'left': '.25', 'right': '.25', 'top': '.4', 'bottom': '.4', 'header': '.2', 'footer': '.2'})
    _node(sheet, S, 'pageSetup', {'paperSize': '9', 'orientation': 'landscape', 'fitToWidth': '1', 'fitToHeight': '0'})
    _node(sheet, S, 'drawing', {'{'+R+'}id': 'rIdDataChart'})
    types = ET.fromstring(parts['[Content_Types].xml'])
    for name, media in [('xl/charts/chart1.xml', 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'), ('xl/drawings/drawing1.xml', 'application/vnd.openxmlformats-officedocument.drawing+xml')]:
        _node(types, CT, 'Override', {'PartName': '/'+name, 'ContentType': media})
    parts['[Content_Types].xml'] = _xml(types)


def _check_updated_forecast_series(root):
    """Keep the role/bounds invariants when editing our explicitly labeled chart.

    This is a constraint check, not an assertion of source or model accuracy.
    """
    series = root.findall('.//c:lineChart/c:ser', NS)
    labels = [item.findtext('c:tx/c:v', namespaces=NS) for item in series]
    if labels[:2] != FORECAST_LABELS[:2]:
        return
    require(labels == FORECAST_LABELS[:len(labels)] and len(labels) in (2, 4), 'Forecast chart role metadata changed')
    points = [{int(p.get('idx')): Decimal(p.findtext('c:v', namespaces=NS)) for p in item.findall('c:val/c:numRef/c:numCache/c:pt', NS)} for item in series]
    observed, predicted = set(points[0]), set(points[1])
    categories = [item.findtext('c:v', namespaces=NS) for item in series[0].findall('c:cat/c:strRef/c:strCache/c:pt', NS)]
    require(observed and predicted and not observed & predicted and observed | predicted == set(range(len(categories))) and max(observed) < min(predicted), 'Forecast edit would mix observed and predicted rows')
    frequency = 'month' if re.fullmatch(r'[0-9]{4}-[0-9]{2}', categories[0] or '') else 'day'
    periods = [_period(value, frequency) for value in categories]
    require(all(b-a == 1 for a, b in zip(periods, periods[1:])), 'Forecast edit would break chronological periods')
    if len(points) == 4:
        require(set(points[2]) == predicted and set(points[3]) == predicted and all(points[2][i] <= points[1][i] <= points[3][i] for i in predicted), 'Forecast edit exceeds supplied bounds; revise values and bounds explicitly')


def _refresh_chart_caches(parts, sheet_name, table, calculated):
    """Refresh only same-sheet, one-column, bounded chart references.

    This is our deterministic update path, not a claim that every target viewer
    automatically refreshes imported chart caches when its own cell UI changes.
    """
    matrix = [table['columns']] + table['rows']
    changed = []
    for part in sorted(parts):
        if '/charts/' not in part or not part.endswith('.xml'):
            continue
        root = ET.fromstring(parts[part])
        references = root.findall('.//c:numRef', NS) + root.findall('.//c:strRef', NS)
        if not references:
            continue
        for reference in references:
            formula = reference.findtext('c:f', default='', namespaces=NS)
            match = re.fullmatch(r"(?:'((?:[^']|'')+)'|([^!]+))!\$?([A-Z])\$?([1-9][0-9]{0,3})(?::\$?([A-Z])\$?([1-9][0-9]{0,3}))?", formula)
            require(match is not None, 'Chart references require a bounded same-sheet range before editing')
            name = (match[1].replace("''", "'") if match[1] is not None else match[2])
            first, last = int(match[4]), int(match[6] or match[4])
            column = ord(match[3])-65
            require(name == sheet_name and (match[5] is None or match[5] == match[3]) and first <= last <= len(matrix) and column < len(table['columns']), 'Chart reference is outside the selected worksheet')
            numeric = reference.tag == '{'+C+'}numRef'
            cache_tag = 'numCache' if numeric else 'strCache'
            old_cache = reference.find('c:'+cache_tag, NS)
            if old_cache is not None:
                reference.remove(old_cache)
            cache = _node(reference, C, cache_tag)
            if numeric:
                _node(cache, C, 'formatCode', value='General')
            _node(cache, C, 'ptCount', {'val': str(last-first+1)})
            for index, row in enumerate(range(first, last+1)):
                value = matrix[row-1][column]
                location = addr(row, column)
                if location in table['formulas']:
                    value = Decimal(calculated['calculated_values'][location])
                if numeric:
                    require(value is None or number(value) is not None, 'Chart numeric source contains text')
                    if value is None:
                        continue
                    value = decstr(number(value))
                else:
                    value = '' if value is None else str(value)
                _node(_node(cache, C, 'pt', {'idx': str(index)}), C, 'v', value=value)
        _check_updated_forecast_series(root)
        if root.find('.//c:barChart', NS) is not None:
            values = [Decimal(node.text) for node in root.findall('.//c:barChart/c:ser/c:val/c:numRef/c:numCache/c:pt/c:v', NS)]
            for axis in root.findall('.//c:valAx/c:scaling', NS):
                _bar_axis_scaling(axis, values)
        new = _xml(root)
        if new != parts[part]:
            parts[part] = new; changed.append(part)
    return changed


def write_xlsx(table, request, *, template_bytes=None, source_bytes=None, source_summary=None):
    from .document_extract import office_pages
    preserve = source_bytes is not None and request['operation'] == 'formulas'
    raw = source_bytes if preserve else template_bytes
    require(raw is not None, 'XLSX output requires a trusted template from the existing skill_artifacts writer')
    archive = _archive(raw)
    try:
        pages = office_pages('xlsx', archive)
        if preserve:
            require(len(pages) == 1, 'In-place formula preservation is limited to one worksheet; cross-sheet dependencies require another engine')
            require(not source_summary['external_relationships'], 'Workbook with external relationships cannot be edited by this engine')
            require(not any(c.get('formula_type') not in (None, 'normal') for c in source_summary['cells']), 'Shared/array formulas cannot be rewritten by this engine')
            require('xl/calcChain.xml' not in archive.names, 'Existing calculation chain requires an unsupported engine-aware edit')
            require(not source_summary['defined_names'], 'Named-range dependency preservation is not supported for edits')
        else:
            require(len(pages) == 1 and not any(p.startswith(('xl/drawings/', 'xl/charts/', 'xl/externalLinks/')) for p in archive.names), 'Expected a plain one-sheet trusted template')
        name, part = pages[request['sheet_index'] if preserve else 0]
        parts = {p: archive.read(p) for p in archive.names if not p.endswith('/')}
        sheet = archive.tree(part)
        require(sheet.find('s:sheetProtection', NS) is None, 'Protected worksheet is not modified')
        book = archive.tree('xl/workbook.xml')
        require(book.find('s:workbookProtection', NS) is None, 'Protected workbook is not modified')
        calculated = recalculate(table)
        require(not calculated['errors'], 'XLSX export blocked by unresolved/unsupported formula errors; inspect JSON diagnostics or repair explicitly')
        if preserve:
            cells = {cell.get('r'): cell for cell in sheet.findall('s:sheetData/s:row/s:c', NS)}
            selected = {address(edit['cell']) for edit in request['edits']} | set(table['formulas'])
            data = sheet.find('s:sheetData', NS)
            for location in sorted(selected, key=lambda a: (int(a[1:]), a[0])):
                cell = cells.get(location)
                if cell is None:
                    row = next((r for r in data if r.get('r') == location[1:]), None)
                    if row is None:
                        row = _node(data, S, 'row', {'r': location[1:]})
                    cell = _node(row, S, 'c', {'r': location})
                value = table['rows'][int(location[1:])-2][ord(location[0])-65]
                _set_cell(cell, value, table['formulas'].get(location), calculated['calculated_values'].get(location))
            data[:] = sorted(data, key=lambda row: int(row.get('r', '0')))
            for row in data:
                row[:] = sorted(row, key=lambda cell: (cell.get('r', 'Z')[0], cell.tag))
        else:
            name = request['sheet_name']; book.find('s:sheets/s:sheet', NS).set('name', name)
            data = sheet.find('s:sheetData', NS)
            data.clear()
            for row_index, row_values in enumerate([table['columns']] + table['rows'], 1):
                row = _node(data, S, 'row', {'r': str(row_index), 'ht': '26', 'customHeight': '1'})
                for index, value in enumerate(row_values):
                    location = addr(row_index, index)
                    cell = _node(row, S, 'c', {'r': location, 's': '1' if row_index == 1 else '0' if type(value) is str else '2'})
                    _set_cell(cell, value, table['formulas'].get(location), calculated['calculated_values'].get(location))
            dimension = sheet.find('s:dimension', NS)
            if dimension is not None:
                dimension.set('ref', 'A1:'+addr(len(table['rows'])+1, len(table['columns'])-1))
            for old in sheet.findall('s:cols', NS): sheet.remove(old)
            cols = ET.Element('{'+S+'}cols')
            for index, header in enumerate(table['columns'], 1):
                width = min(45, max(16, len(header)*1.2+3, max((len(str(row[index-1])) for row in table['rows'][:100] if row[index-1] is not None), default=0)+2))
                _node(cols, S, 'col', {'min': str(index), 'max': str(index), 'width': str(width), 'customWidth': '1'})
            sheet.insert(list(sheet).index(data), cols)
            auto = sheet.find('s:autoFilter', NS)
            if auto is None: auto = _node(sheet, S, 'autoFilter')
            auto.set('ref', 'A1:'+addr(len(table['rows'])+1, len(table['columns'])-1))
        if request['operation'] == 'chart':
            _chart_parts(parts, part, name, sheet, table, request['chart'])
        refreshed_charts = _refresh_chart_caches(parts, name, table, calculated) if preserve else []
        calc = book.find('s:calcPr', NS)
        if calc is None: calc = _node(book, S, 'calcPr')
        calc.set('calcMode', 'auto'); calc.set('fullCalcOnLoad', '1')
        parts[part] = _xml(sheet); parts['xl/workbook.xml'] = _xml(book)
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as zipped:
            for path in sorted(parts):
                info = ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0)); info.compress_type = ZIP_DEFLATED
                zipped.writestr(info, parts[path])
        content = output.getvalue(); require(len(content) <= MAX_BYTES, 'Output exceeds 8 MiB')
        # Independently reopen the generated bytes via the existing bounded parser.
        reopened, verification = read_xlsx(content, {**request, 'sheet_index': 0 if not preserve else request['sheet_index'], 'header_row': 1})
        require(reopened is not None and reopened['columns'] == table['columns'], 'Saved XLSX headers differ')
        require(reopened['rows'] == table['rows'] and reopened['formulas'] == {key: '=' + value.removeprefix('=') for key, value in table['formulas'].items()}, 'Saved XLSX content differs')
        return DataArtifact(content, 'riwaq-data.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', hashlib.sha256(content).hexdigest(), {'reopened': True, 'formula_calculation': calculated, 'native_editable_chart': request['operation'] == 'chart', 'chart_series_bound_to_cells': request['operation'] == 'chart', 'chart_new_rows_auto_expand': False, 'refreshed_chart_cache_parts': refreshed_charts, 'preserved_original_package': preserve, 'unchanged_package_parts': [name for name in archive.names if name in parts and archive.read(name) == parts[name]] if preserve else [], 'office_rendered': False}, ('XLSX formula support is the declared bounded subset; stored caches are not proof of native Excel recalculation.', 'Charts use fixed real cell ranges; adding rows requires an explicit range update.', 'Visual rendering and target-application compatibility require independent review.'))
    finally:
        archive.zip.close()


def execute_data(request, *, source_bytes=None, template_bytes=None):
    request = validate_request(request)
    require(not (source_bytes is not None and 'table' in request), 'Choose source bytes or a typed table, not both')
    summary, warnings, source_hash = {}, [], None
    if source_bytes is not None:
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        table, summary = (read_xlsx if request['source_format'] == 'xlsx' else read_csv)(source_bytes, request)
        if table is None:
            return DataResult(None, summary, warnings=('Resolve invalid headers explicitly; no data was silently renamed or dropped.',), source_sha256=source_hash)
    else:
        require('table' in request, 'Provide one authorized source or a typed table')
        table = validate_table(request['table'])
    original_summary = summary
    operation = request['operation']
    if request['filters']:
        require(operation in ('profile', 'stats', 'explore', 'aggregate'), 'Filters apply only to analytical operations')
        excluded, kept = [], []
        for row_index, row in enumerate(table['rows']):
            keep = True
            for rule in request['filters']:
                left, right = row[_column(table, rule['column'])], rule['value']
                if rule['operator'] in ('eq', 'ne'):
                    match = type(left) == type(right) and left == right
                    keep &= match if rule['operator'] == 'eq' else not match
                else:
                    require(number(right) is not None, 'Range filter requires numeric boundary')
                    keep &= number(left) is not None and (number(left) >= number(right) if rule['operator'] == 'gte' else number(left) <= number(right))
            (kept if keep else excluded).append(row if keep else row_index)
        require(not table['formulas'], 'Filtered formula references need unsupported remapping')
        table = {**table, 'rows': kept}
        summary['filters'] = {'rules': request['filters'], 'excluded_row_indices': excluded}
    if operation == 'clean':
        table, detail, warnings = clean_table(table, request['cleaning']); summary['cleaning'] = detail
    elif operation == 'join':
        require('right' in request, 'Join requires a separately authorized typed right table')
        table, detail = join_tables(table, request['right'], request['join']); summary['join'] = detail
    elif operation == 'aggregate':
        table, detail = aggregate_table(table, request['aggregate']); summary['aggregation'] = detail
        if request['aggregate']['operation'] not in ('count', 'distinct'):
            for group in detail['groups']:
                warnings.extend(_statistics_warnings(group['statistics'], request['aggregate']['value_column']))
    elif operation == 'formulas':
        table, detail = edit_formulas(table, request['edits']); summary['formula_debugging'] = detail
    elif operation in ('profile', 'stats', 'explore'):
        summary['profile'] = profile(table)
        require(not table['formulas'] or operation == 'profile', 'Statistics do not treat unresolved formulas/caches as fresh inputs')
        if operation in ('stats', 'explore'):
            selected = request['columns'] or [name for name, field in summary['profile']['fields'].items() if field['numeric_count']]
            summary['statistics'] = {}
            for name in selected:
                index = _column(table, name)
                weights = [r[_column(table, request['weight_column'])] for r in table['rows']] if 'weight_column' in request else None
                summary['statistics'][name] = statistics([r[index] for r in table['rows']], ddof=request['ddof'], quantiles=request['quantiles'], weights=weights)
                warnings.extend(_statistics_warnings(summary['statistics'][name], name))
            summary['record_unit'] = request.get('record_unit', 'not specified')
            if operation == 'explore':
                summary['interpretation'] = 'Descriptive patterns only; no causal inference or automatic outlier deletion'
                if 'forecast_review' in request:
                    summary['forecast_review'] = forecast_review(table, request['forecast_review'])
                    warnings.append('Forecast diagnostics and a chronological plan only; no fitted model, evaluated accuracy or guaranteed predictions.')
                if 'aggregate' in request:
                    grouped, detail = aggregate_table(table, request['aggregate']); summary['grouped_table'] = grouped; summary['grouped_analysis'] = detail
    elif operation in ('read', 'table'):
        summary['profile'] = profile(table)
    elif operation == 'chart' and 'forecast' in request['chart']:
        summary['forecast_display'] = forecast_display(table, request['chart'])
        warnings.append('Forecast series and optional bounds are supplied scenario data; no forecast model or calibrated confidence was produced.')
    artifact = None
    output = 'xlsx' if operation == 'chart' else request['format']
    if output == 'xlsx':
        artifact = write_xlsx(table, request, template_bytes=template_bytes, source_bytes=source_bytes, source_summary=original_summary)
    elif output == 'csv' or operation == 'csv' and source_bytes is None:
        artifact = export_csv(table, request)
    summary['rows'] = len(table['rows'])
    summary['operation'] = operation
    summary['version'] = VERSION
    return DataResult(table, summary, artifact, tuple(warnings), source_hash)
