"""Explicit bounded civil-time contracts. No clock reads, scheduling or authority.

An instant is not a local wall time and neither is an all-day date. Local times
are round-tripped through UTC to reject DST gaps and require fold selection.
The pinned first-party tzdata package is preferred for reproducible IANA rules.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from importlib import metadata, resources
import re
import unicodedata
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import zoneinfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UTC = timezone.utc
LOCAL = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$')
INSTANT = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
ZONE = re.compile(r'^[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*$')


class TimeResolutionError(ValueError):
    def __init__(self, code, *, candidates=None):
        super().__init__(code)
        self.code = code
        self.candidates = candidates or []


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TimeInput(Strict):
    kind: Literal['instant', 'local', 'date']
    value: str = Field(min_length=1, max_length=40)
    zone: str | None = Field(default=None, min_length=1, max_length=100)
    fold: Literal[0, 1] | None = None

    @field_validator('fold', mode='before')
    @classmethod
    def fold_is_integer(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError('fold must be integer 0 or 1')
        return value

    @model_validator(mode='after')
    def validate_kind(self):
        if self.kind != 'local' and self.fold is not None:
            raise ValueError('fold applies only to local wall time')
        return self


class RecurrenceRule(Strict):
    frequency: Literal['daily', 'weekly'] = 'daily'
    interval: int = Field(default=1, ge=1, le=30)
    weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)
    count: int = Field(default=366, ge=1, le=366)
    until_local: str | None = Field(default=None, max_length=32)
    fold: Literal[0, 1] | None = None
    nonexistent: Literal['reject', 'skip'] = 'reject'

    @field_validator('weekdays')
    @classmethod
    def valid_weekdays(cls, value):
        if value is not None and (any(not 0 <= x <= 6 for x in value) or len(set(value)) != len(value)):
            raise ValueError('Unique ISO weekdays 0..6 required')
        return value

    @field_validator('fold', mode='before')
    @classmethod
    def valid_fold(cls, value):
        return TimeInput.fold_is_integer(value)

    @model_validator(mode='after')
    def weekday_scope(self):
        if self.frequency == 'daily' and self.weekdays is not None:
            raise ValueError('Weekday selection requires weekly recurrence')
        return self


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise TimeResolutionError('explicit_iso_date_required')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise TimeResolutionError('invalid_date') from exc


def parse_local(value: str) -> datetime:
    if not isinstance(value, str) or not LOCAL.fullmatch(value):
        raise TimeResolutionError('explicit_local_datetime_required')
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise TimeResolutionError('invalid_local_datetime') from exc


def parse_instant(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TimeResolutionError('aware_instant_required')
        return value.astimezone(UTC)
    if not isinstance(value, str) or not INSTANT.fullmatch(value):
        raise TimeResolutionError('explicit_offset_required')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
    except ValueError as exc:
        raise TimeResolutionError('invalid_instant') from exc


def iso_utc(value: datetime) -> str:
    return parse_instant(value).isoformat(timespec='microseconds' if value.microsecond else 'seconds').replace('+00:00', 'Z')


def _zone_key(key):
    if not isinstance(key, str) or len(key) > 100 or not ZONE.fullmatch(key):
        raise TimeResolutionError('invalid_iana_zone')
    return key


@lru_cache(maxsize=128)
def _load_zone(key):
    _zone_key(key)
    try:
        import tzdata
    except ImportError:
        if key in {'UTC', 'Etc/UTC'}:
            return UTC, {'source': 'stdlib fixed UTC', 'iana_version': None, 'package_version': None}
        try:
            return ZoneInfo(key), {'source': 'system IANA data', 'iana_version': None, 'package_version': None}
        except ZoneInfoNotFoundError as exc:
            code = 'timezone_data_unavailable' if not zoneinfo.TZPATH else 'unknown_iana_zone'
            raise TimeResolutionError(code) from exc
    try:
        with resources.files('tzdata.zoneinfo').joinpath(*key.split('/')).open('rb') as stream:
            result = ZoneInfo.from_file(stream, key=key)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        raise TimeResolutionError('unknown_iana_zone') from exc
    return result, {'source': 'tzdata package', 'iana_version': getattr(tzdata, 'IANA_VERSION', None),
                    'package_version': metadata.version('tzdata')}


def zone_data(zone: str) -> dict:
    return dict(_load_zone(zone)[1])


def get_zone(zone: str):
    """Use the same pinned zone rules for display and temporal validation."""
    return _load_zone(zone)[0]


def resolve_local(local_iso: str, zone: str, fold: int | None = None) -> datetime:
    """Resolve one explicit wall time to UTC. Never shift a nonexistent time."""
    naive = parse_local(local_iso)
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise TimeResolutionError('invalid_fold')
    tz, _ = _load_zone(zone)
    candidates = {}
    for choice in (0, 1):
        candidate = naive.replace(tzinfo=tz, fold=choice).astimezone(UTC)
        roundtrip = candidate.astimezone(tz)
        if roundtrip.replace(tzinfo=None) == naive:
            candidates[choice] = candidate
    unique = sorted(set(candidates.values()))
    if not unique:
        raise TimeResolutionError('nonexistent_local_time')
    if len(unique) > 1 and fold is None:
        raise TimeResolutionError('ambiguous_local_time', candidates=[
            {'fold': n, 'utc': iso_utc(value)} for n, value in candidates.items()])
    if len(unique) == 1:
        return unique[0]
    return candidates[fold]


def normalize_time(value: TimeInput | dict, *, default_zone: str | None = None) -> dict:
    value = value if isinstance(value, TimeInput) else TimeInput.model_validate(value)
    zone = value.zone or default_zone
    if value.kind == 'date':
        return {'kind': 'date', 'date': parse_date(value.value).isoformat(), 'zone': zone,
                'utc': None, 'tzdb': zone_data(zone) if zone else None}
    if value.kind == 'local':
        if not zone:
            raise TimeResolutionError('explicit_iana_zone_required')
        instant = resolve_local(value.value, zone, value.fold)
    else:
        instant = parse_instant(value.value)
        zone = zone or 'UTC'  # Explicit offset already determines the instant.
    tz, version = _load_zone(zone)
    local = instant.astimezone(tz)
    return {'kind': value.kind, 'utc': iso_utc(instant), 'local': local.replace(tzinfo=None).isoformat(),
            'zone': zone, 'fold': local.fold, 'offset_seconds': int(local.utcoffset().total_seconds()), 'tzdb': dict(version)}


def normalize_interval(start: TimeInput | dict, end: TimeInput | dict, *, default_zone=None) -> dict:
    left = normalize_time(start, default_zone=default_zone)
    right = normalize_time(end, default_zone=default_zone)
    if left['kind'] == 'date' or right['kind'] == 'date':
        if left['kind'] != right['kind']:
            raise TimeResolutionError('mixed_date_and_instant_interval')
        if left['zone'] != right['zone']:
            raise TimeResolutionError('all_day_zone_mismatch')
        if left['date'] >= right['date']:
            raise TimeResolutionError('positive_all_day_interval_required')
        return {'kind': 'all_day', 'start_date': left['date'], 'end_date_exclusive': right['date'],
                'zone': left['zone'], 'start_utc': None, 'end_utc': None, 'tzdb': left['tzdb']}
    seconds = (parse_instant(right['utc']) - parse_instant(left['utc'])).total_seconds()
    if seconds <= 0:
        raise TimeResolutionError('positive_timed_interval_required')
    return {'kind': 'timed', 'start_utc': left['utc'], 'end_utc': right['utc'],
            'elapsed_seconds': seconds, 'start': left, 'end': right, 'end_exclusive': True}


def local_recurrence(start_local: str, zone: str, rule: RecurrenceRule | dict | None = None, **kwargs) -> dict:
    """Bounded local dates, not repeated UTC+24h. count counts emitted occurrences."""
    if rule is not None and kwargs:
        raise TimeResolutionError('choose_one_recurrence_contract')
    rule = rule if isinstance(rule, RecurrenceRule) else RecurrenceRule.model_validate(rule or kwargs)
    anchor = parse_local(start_local)
    until = parse_local(rule.until_local) if rule.until_local else None
    if until is not None and until < anchor:
        raise TimeResolutionError('recurrence_until_before_start')
    zone_data(zone)
    weekdays = sorted(rule.weekdays or [anchor.weekday()])
    week_start = anchor.date() - timedelta(days=anchor.weekday())
    occurrences = []
    skipped = []
    examined = 0
    stop = False
    for period in range(rule.count + 366):
        try:
            candidates = ([anchor + timedelta(days=period * rule.interval)] if rule.frequency == 'daily' else [
                datetime.combine(week_start + timedelta(days=period * rule.interval * 7 + weekday), anchor.time()) for weekday in weekdays])
        except OverflowError as exc:
            raise TimeResolutionError('recurrence_date_out_of_range') from exc
        for candidate in candidates:
            if candidate < anchor:
                continue
            if until is not None and candidate > until:
                stop = True
                break
            examined += 1
            if examined > rule.count + 366:
                raise TimeResolutionError('recurrence_candidate_limit')
            local = candidate.isoformat()
            try:
                result = normalize_time({'kind': 'local', 'value': local, 'zone': zone, 'fold': rule.fold})
            except TimeResolutionError as exc:
                if exc.code == 'nonexistent_local_time' and rule.nonexistent == 'skip':
                    skipped.append({'local': local, 'code': exc.code})
                    continue
                raise
            occurrences.append(result)
            if len(occurrences) == rule.count:
                stop = True
                break
        if stop:
            break
    return {'occurrences': occurrences, 'skipped': skipped, 'rule': rule.model_dump(),
            'anchor_local': anchor.isoformat(), 'zone': zone, 'tzdb': zone_data(zone),
            'count_semantics': 'emitted occurrences; explicitly skipped gaps do not count', 'examined': examined}


def local_daily_occurrences(start_local: str, zone: str, **kwargs) -> dict:
    return local_recurrence(start_local, zone, {'frequency': 'daily', **kwargs})


def next_occurrence(start_local: str, zone: str, rule: RecurrenceRule | dict, after_utc=None) -> dict | None:
    after = parse_instant(after_utc) if after_utc is not None else None
    for occurrence in local_recurrence(start_local, zone, rule)['occurrences']:
        if after is None or parse_instant(occurrence['utc']) > after:
            return occurrence
    return None


def relative_date(expression: str, context_date: str | None, zone: str, weekStartsOn: int | None = None) -> dict:
    """Small explicit ar/he/en vocabulary anchored to caller's reference date."""
    zone_data(zone)
    if context_date is None:
        return {'kind': 'ambiguous', 'reason': 'reference_date_required', 'zone': zone}
    anchor = parse_date(context_date)
    if not isinstance(expression, str) or len(expression) > 120:
        raise TimeResolutionError('relative_expression_limit')
    text = ' '.join(''.join(x for x in unicodedata.normalize('NFC', expression.casefold()) if not unicodedata.combining(x)).split())
    if text in {'today', 'اليوم', 'היום'}:
        result = anchor
    elif text in {'tomorrow', 'غدا', 'بكرا', 'بكرة', 'מחר'}:
        result = anchor + timedelta(days=1)
    elif text in {'next week', 'الأسبوع القادم', 'الاسبوع القادم', 'الأسبوع الجاي', 'الاسبوع الجاي', 'שבוע הבא', 'בשבוע הבא'}:
        if weekStartsOn is None:
            return {'kind': 'ambiguous', 'reason': 'week_start_required', 'reference_date': context_date, 'zone': zone}
        if type(weekStartsOn) is not int or not 0 <= weekStartsOn <= 6:
            raise TimeResolutionError('invalid_week_start')
        first = anchor - timedelta(days=(anchor.weekday() - weekStartsOn) % 7) + timedelta(days=7)
        return {'kind': 'range', 'start_date': first.isoformat(), 'end_date_exclusive': (first + timedelta(days=7)).isoformat(),
                'reference_date': context_date, 'zone': zone, 'week_starts_on': weekStartsOn}
    elif DATE.fullmatch(text):
        result = parse_date(text)
    else:
        return {'kind': 'ambiguous', 'reason': 'expression_outside_tested_vocabulary', 'reference_date': context_date, 'zone': zone}
    return {'kind': 'date', 'date': result.isoformat(), 'reference_date': context_date, 'zone': zone, 'utc': None}
