"""Deterministic bounded planning proposals. Never writes calendars or sends work.

Availability is explicit, allocation uses elapsed UTC time, and labels are data.
This is a greedy feasible planner, not a global optimum or natural-language LLM.
The caller owns identity, storage, cancellation, approval and publication.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from pydantic import Field, field_validator, model_validator
from backend.productivity_time import (
    Strict, TimeInput, TimeResolutionError, UTC, _load_zone, iso_utc,
    normalize_interval, normalize_time, parse_date, parse_instant, parse_local,
    resolve_local, zone_data,
)


class Window(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    start: TimeInput
    end: TimeInput


class Commitment(Window):
    title: str = Field(default='', max_length=250)
    bufferBeforeMinutes: int = Field(default=0, ge=0, le=360)
    bufferAfterMinutes: int = Field(default=0, ge=0, le=360)


class PlanTask(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    title: str = Field(min_length=1, max_length=250)
    minutes: int | None = Field(default=None, ge=0, le=10080)
    priority: int = Field(default=0, ge=0, le=100)
    splittable: bool = False
    minBlockMinutes: int = Field(default=15, ge=1, le=1440)
    dependsOn: list[str] = Field(default_factory=list, max_length=30)
    earliest: TimeInput | None = None
    deadline: TimeInput | None = None
    completed: bool = False


class ShiftInput(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    title: str = Field(default='', max_length=250)
    startDate: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    startTime: str = Field(pattern=r'^\d{2}:\d{2}$')
    endTime: str = Field(pattern=r'^\d{2}:\d{2}$')
    endNextDay: bool | None = None
    commuteBeforeMinutes: int | None = Field(default=None, ge=0, le=240)
    commuteAfterMinutes: int | None = Field(default=None, ge=0, le=240)
    restAfterMinutes: int | None = Field(default=None, ge=0, le=2880)
    foldStart: Literal[0, 1] | None = None
    foldEnd: Literal[0, 1] | None = None

    @field_validator('foldStart', 'foldEnd', mode='before')
    @classmethod
    def valid_fold(cls, value):
        return TimeInput.fold_is_integer(value)


class ParticipantAvailability(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    label: str = Field(default='', max_length=250)
    zone: str = Field(min_length=1, max_length=100)
    known: bool = True
    windows: list[Window] = Field(default_factory=list, max_length=64)
    busy: list[Commitment] = Field(default_factory=list, max_length=128)


class Contingency(Strict):
    unavailable: list[Window] = Field(default_factory=list, max_length=64)
    addedCommitments: list[Commitment] = Field(default_factory=list, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class AgendaItem(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    title: str = Field(min_length=1, max_length=500)
    minutes: int | None = Field(default=None, ge=0, le=1440)
    owner: str | None = Field(default=None, max_length=200)
    dependsOn: list[str] = Field(default_factory=list, max_length=30)


class Agenda(Strict):
    title: str = Field(min_length=1, max_length=250)
    purpose: str = Field(default='', max_length=1000)
    items: list[AgendaItem] = Field(default_factory=list, max_length=50)
    durationMinutes: int | None = Field(default=None, ge=0, le=1440)


class PlanningRequest(Strict):
    operation: Literal['daily', 'weekly', 'shift', 'conflicts', 'meeting-search', 'replan', 'time-blocking', 'agenda', 'timezone']
    zone: str = Field(min_length=1, max_length=100)
    startDate: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    weekStartsOn: int | None = Field(default=None, ge=0, le=6)
    windows: list[Window] = Field(default_factory=list, max_length=64)
    commitments: list[Commitment] = Field(default_factory=list, max_length=128)
    tasks: list[PlanTask] = Field(default_factory=list, max_length=100)
    shifts: list[ShiftInput] = Field(default_factory=list, max_length=31)
    participants: list[ParticipantAvailability] = Field(default_factory=list, max_length=20)
    durationMinutes: int | None = Field(default=None, ge=1, le=1440)
    scenario: Contingency | None = None
    agenda: Agenda | None = None
    times: list[TimeInput] = Field(default_factory=list, max_length=64)
    displayZones: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def unique_and_scoped(self):
        for name in ('windows', 'commitments', 'tasks', 'shifts', 'participants'):
            ids = [x.id for x in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate '+name+' identifier')
        ids = {x.id for x in self.tasks}
        for task in self.tasks:
            if len(set(task.dependsOn)) != len(task.dependsOn) or task.id in task.dependsOn or any(x not in ids for x in task.dependsOn):
                raise ValueError('Task dependency is duplicate, self-referential or missing')
        if self.scenario is not None and self.operation != 'replan':
            raise ValueError('Contingency requires replan operation')
        if self.agenda is not None and self.operation != 'agenda':
            raise ValueError('Agenda input requires agenda operation')
        if self.participants and self.operation != 'meeting-search':
            raise ValueError('Participants require meeting-search operation')
        if (self.times or self.displayZones) and self.operation != 'timezone':
            raise ValueError('Time conversion inputs require timezone operation')
        if len(set(self.displayZones)) != len(self.displayZones) or any(not x or len(x)>100 for x in self.displayZones):
            raise ValueError('Unique bounded display zones required')
        if self.operation in {'agenda', 'timezone'} and (self.tasks or self.windows or self.commitments or self.shifts):
            raise ValueError('Planning inputs do not belong to this operation')
        if self.operation in {'conflicts', 'meeting-search'} and self.tasks:
            raise ValueError('Task allocation requires a planning operation')
        if self.durationMinutes is not None and self.operation != 'meeting-search':
            raise ValueError('Meeting duration belongs to meeting-search; agenda has its own capacity')
        if self.agenda and len({x.id for x in self.agenda.items}) != len(self.agenda.items):
            raise ValueError('Duplicate agenda item')
        if self.agenda:
            ids = {x.id for x in self.agenda.items}
            for item in self.agenda.items:
                if len(set(item.dependsOn)) != len(item.dependsOn) or item.id in item.dependsOn or any(x not in ids for x in item.dependsOn):
                    raise ValueError('Agenda dependency is duplicate, self-referential or missing')
        return self


def _point(value, zone):
    result = normalize_time(value, default_zone=zone)
    if result['kind'] == 'date':
        raise TimeResolutionError('explicit_time_required_for_task_bound')
    return parse_instant(result['utc'])


def _bounds(value, zone, *, allow_date=False):
    result = normalize_interval(value.start, value.end, default_zone=zone)
    if result['kind'] == 'all_day':
        if not allow_date:
            raise TimeResolutionError('working_hours_required_for_date')
        actual_zone = result['zone']
        if not actual_zone:
            raise TimeResolutionError('explicit_iana_zone_required')
        left = resolve_local(result['start_date']+'T00:00:00', actual_zone)
        right = resolve_local(result['end_date_exclusive']+'T00:00:00', actual_zone)
    else:
        left, right = parse_instant(result['start_utc']), parse_instant(result['end_utc'])
    if right-left > timedelta(days=31):
        raise TimeResolutionError('interval_exceeds_31_days')
    return left, right


def _merged(intervals):
    out = []
    for left, right in sorted(intervals):
        if left >= right:
            continue
        if out and left <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], right))
        else:
            out.append((left, right))
    return out


def _subtract(intervals, excluded):
    output = _merged(intervals)
    for start, end in _merged(excluded):
        pending = []
        for left, right in output:
            if end <= left or start >= right:
                pending.append((left, right))
            else:
                if left < start:
                    pending.append((left, start))
                if right > end:
                    pending.append((end, right))
        output = pending
    return output


def _intersection(left, right):
    return _merged([(max(a, c), min(b, d)) for a, b in left for c, d in right if max(a, c) < min(b, d)])


def _serialized(intervals, zone):
    tz, _ = _load_zone(zone)
    return [{'start_utc': iso_utc(a), 'end_utc': iso_utc(b),
             'start_local': a.astimezone(tz).isoformat(), 'end_local': b.astimezone(tz).isoformat(),
             'minutes': (b-a).total_seconds()/60} for a, b in intervals]


def _commitments(values, zone):
    output = []
    for event in values:
        start, end = _bounds(event, zone, allow_date=True)
        output.append({'id': event.id, 'title': event.title, 'kind': 'commitment', 'raw_start': start, 'raw_end': end,
                       'start': start-timedelta(minutes=event.bufferBeforeMinutes),
                       'end': end+timedelta(minutes=event.bufferAfterMinutes)})
    return output


def _shifts(values, zone):
    output, records, ambiguities, uncertainties = [], [], [], []
    for shift in values:
        start_date = parse_date(shift.startDate)
        start_naive = parse_local(shift.startDate+'T'+shift.startTime)
        end_naive = parse_local(shift.startDate+'T'+shift.endTime)
        if shift.endNextDay is None and end_naive <= start_naive:
            ambiguities.append({'field': 'shifts.'+shift.id+'.endNextDay', 'code': 'overnight_next_day_required'})
            continue
        if shift.endNextDay:
            end_naive += timedelta(days=1)
        if end_naive-start_naive > timedelta(days=1):
            raise TimeResolutionError('shift_must_be_positive_and_at_most_one_civil_day')
        start = resolve_local(start_naive.isoformat(), zone, shift.foldStart)
        end = resolve_local(end_naive.isoformat(), zone, shift.foldEnd)
        if end <= start:
            raise TimeResolutionError('positive_shift_elapsed_duration_required')
        missing = [name for name in ('commuteBeforeMinutes', 'commuteAfterMinutes', 'restAfterMinutes') if getattr(shift, name) is None]
        if missing:
            uncertainties.append({'shift_id': shift.id, 'fields': missing, 'code': 'undeclared_shift_constraints',
                                  'notice': 'Unspecified travel/rest is not included in provisional availability; verify before booking'})
        before = start-timedelta(minutes=shift.commuteBeforeMinutes or 0)
        commute_end = end+timedelta(minutes=shift.commuteAfterMinutes or 0)
        rest_end = commute_end+timedelta(minutes=shift.restAfterMinutes or 0)
        output.append({'id': shift.id, 'title': shift.title, 'kind': 'shift', 'raw_start': start, 'raw_end': end,
                       'start': before, 'end': rest_end})
        records.append({'id': shift.id, 'title': shift.title, 'start_date': start_date.isoformat(),
                        'end_next_day': bool(shift.endNextDay), 'work': _serialized([(start, end)], zone)[0],
                        'commute_before_minutes': shift.commuteBeforeMinutes, 'commute_after_minutes': shift.commuteAfterMinutes,
                        'rest_after_minutes': shift.restAfterMinutes, 'protected_until_utc': iso_utc(rest_end)})
    return output, records, ambiguities, uncertainties


def _conflicts(events):
    items, checked = [], 0
    for index, left in enumerate(events):
        for right in events[index+1:]:
            checked += 1
            start, end = max(left['start'], right['start']), min(left['end'], right['end'])
            if start < end and len(items) < 512:
                actual = max(left['raw_start'], right['raw_start']) < min(left['raw_end'], right['raw_end'])
                items.append({'left_id': left['id'], 'right_id': right['id'], 'left_kind': left['kind'], 'right_kind': right['kind'],
                              'kind': 'actual_overlap' if actual else 'buffer_or_rest_overlap',
                              'start_utc': iso_utc(start), 'end_utc': iso_utc(end), 'minutes': (end-start).total_seconds()/60})
    return {'items': items, 'pairs_checked': checked, 'limit': 512, 'truncated': len(items) == 512,
            'scope': 'Only supplied intervals and explicit travel/rest buffers; end is exclusive'}


def _windows(request):
    output = _merged([_bounds(w, request.zone) for w in request.windows])
    if output and output[-1][1]-output[0][0] > timedelta(days=32):
        raise TimeResolutionError('planning_horizon_exceeds_32_days')
    if output and request.operation == 'daily' and not request.startDate:
        tz, _ = _load_zone(request.zone)
        dates = {x.astimezone(tz).date() for a, b in output for x in (a, b-timedelta(microseconds=1))}
        if len(dates) != 1:
            raise TimeResolutionError('daily_target_date_required_for_multiple_days')
    if request.startDate:
        date_start = parse_date(request.startDate)
        if request.operation == 'weekly' and request.weekStartsOn is not None and date_start.weekday() != request.weekStartsOn:
            raise TimeResolutionError('start_date_does_not_match_chosen_week_start')
        days = 7 if request.operation == 'weekly' else 1 if request.operation == 'daily' else 31
        period = [(resolve_local(date_start.isoformat()+'T00:00:00', request.zone),
                   resolve_local((date_start+timedelta(days=days)).isoformat()+'T00:00:00', request.zone))]
        output = _intersection(output, period)
    return output


def _ordered(tasks):
    remaining = list(enumerate(tasks))
    result, handled = [], set()
    while remaining:
        ready = [(i, t) for i, t in remaining if all(d in handled for d in t.dependsOn)]
        if not ready:
            raise TimeResolutionError('cyclic_task_dependencies')
        index, chosen = min(ready, key=lambda pair: (-pair[1].priority, pair[0]))
        result.append(chosen)
        handled.add(chosen.id)
        remaining = [(i, t) for i, t in remaining if i != index]
    return result


def _allocate(task, free, lower, upper):
    slots = [(max(a, lower) if lower else a, min(b, upper) if upper else b) for a, b in free]
    slots = [(a, b) for a, b in slots if a < b]
    required = timedelta(minutes=task.minutes)
    if task.minutes == 0:
        return [(slots[0][0], slots[0][0])] if slots else None
    if not task.splittable:
        return next(([(a, a+required)] for a, b in slots if b-a >= required), None)
    pieces = []
    remaining = required
    minimum = timedelta(minutes=task.minBlockMinutes)
    for a, b in slots:
        take = min(b-a, remaining)
        if take < minimum and take != remaining:
            continue
        pieces.append((a, a+take))
        remaining -= take
        if remaining == timedelta(0):
            return pieces
    return None  # Atomic proposal: do not consume fragments of an unfinished task.


def _schedule(request, free):
    scheduled, deferred, done, ambiguities = [], [], {}, []
    completed = [t.model_dump() for t in request.tasks if t.completed]
    failed = set()
    for task in _ordered(request.tasks):
        if task.completed:
            done[task.id] = None
            continue
        if any(dep in failed for dep in task.dependsOn):
            deferred.append({'task_id': task.id, 'title': task.title, 'minutes': task.minutes, 'reason': 'dependency_not_scheduled'})
            failed.add(task.id)
            continue
        if task.minutes is None:
            deferred.append({'task_id': task.id, 'title': task.title, 'minutes': None, 'reason': 'duration_unknown'})
            ambiguities.append({'field': 'tasks.'+task.id+'.minutes', 'code': 'duration_required'})
            failed.add(task.id)
            continue
        try:
            earliest = _point(task.earliest, request.zone) if task.earliest else None
            deadline = _point(task.deadline, request.zone) if task.deadline else None
        except TimeResolutionError as exc:
            deferred.append({'task_id': task.id, 'title': task.title, 'minutes': task.minutes, 'reason': 'task_time_unresolved'})
            ambiguities.append({'field': 'tasks.'+task.id, 'code': exc.code, 'candidates': exc.candidates})
            failed.add(task.id)
            continue
        dependencies = [done[d] for d in task.dependsOn if done.get(d) is not None]
        if dependencies:
            earliest = max(dependencies+([earliest] if earliest else []))
        if earliest is not None and deadline is not None and earliest > deadline:
            deferred.append({'task_id': task.id, 'title': task.title, 'minutes': task.minutes, 'reason': 'deadline_before_earliest_or_dependency'})
            failed.add(task.id)
            continue
        pieces = _allocate(task, free, earliest, deadline)
        if pieces is None:
            deferred.append({'task_id': task.id, 'title': task.title, 'minutes': task.minutes,
                             'reason': 'insufficient_compatible_time' if task.splittable else 'contiguous_slot_unavailable'})
            failed.add(task.id)
            continue
        scheduled.append({'task_id': task.id, 'title': task.title, 'minutes': task.minutes,
                          'kind': 'milestone' if task.minutes == 0 else 'time_block', 'split': len(pieces)>1,
                          'depends_on': list(task.dependsOn), 'blocks': _serialized(pieces, request.zone)})
        done[task.id] = pieces[-1][1]
        free = _subtract(free, pieces)
    return {'scheduled': scheduled, 'deferred': deferred, 'completed_tasks_unchanged': completed,
            'ambiguities': ambiguities, 'free_windows': _serialized(free, request.zone),
            'metrics': {'scheduled_minutes': sum(t['minutes'] for t in scheduled),
                        'deferred_known_minutes': sum(t['minutes'] for t in deferred if t['minutes'] is not None),
                        'unknown_duration_count': sum(t['minutes'] is None for t in deferred),
                        'remaining_free_minutes': sum((b-a).total_seconds()/60 for a, b in free)}}


def _planning(request, *, extra=()):
    windows = _windows(request)
    commitments = _commitments(request.commitments, request.zone)
    shift_events, shifts, ambiguities, uncertainties = _shifts(request.shifts, request.zone)
    events = commitments+shift_events+list(extra)
    free = _subtract(windows, [(x['start'], x['end']) for x in events])
    conflicts = _conflicts(events)
    if request.operation == 'weekly' and not request.startDate:
        ambiguities.append({'field': 'startDate', 'code': 'explicit_week_start_date_required'})
    if request.operation not in {'conflicts', 'shift'} and not windows:
        ambiguities.append({'field': 'windows', 'code': 'explicit_working_windows_required'})
    if ambiguities:
        scheduled = {'scheduled': [], 'deferred': [{'task_id': t.id, 'title': t.title, 'minutes': t.minutes, 'reason': 'time_context_unresolved'} for t in request.tasks if not t.completed],
                     'completed_tasks_unchanged': [t.model_dump() for t in request.tasks if t.completed], 'ambiguities': ambiguities,
                     'free_windows': [], 'metrics': {'scheduled_minutes': 0, 'remaining_free_minutes': None}}
    else:
        scheduled = _schedule(request, free)
    scheduled.update(shifts=shifts, conflicts=conflicts, uncertainties=uncertainties)
    scheduled['metrics'].update(available_before_tasks_minutes=sum((b-a).total_seconds()/60 for a, b in free),
                                availability_minutes=sum((b-a).total_seconds()/60 for a, b in windows))
    return scheduled


def _meeting(request):
    ambiguities = []
    if len(request.participants) < 2:
        ambiguities.append({'field': 'participants', 'code': 'two_participant_availabilities_required'})
    if request.durationMinutes is None:
        ambiguities.append({'field': 'durationMinutes', 'code': 'meeting_duration_required'})
    overlap = None
    for participant in request.participants:
        if not participant.known:
            ambiguities.append({'field': 'participants.'+participant.id, 'code': 'availability_unknown_not_assumed_free'})
            continue
        zone_data(participant.zone)
        windows = _merged([_bounds(x, participant.zone) for x in participant.windows])
        busy = _commitments(participant.busy, participant.zone)
        free = _subtract(windows, [(x['start'], x['end']) for x in busy])
        overlap = free if overlap is None else _intersection(overlap, free)
    if ambiguities:
        return {'ambiguities': ambiguities, 'candidates': [], 'common_windows': [], 'availability_scope': 'incomplete'}
    if request.windows:
        overlap = _intersection(overlap or [], _windows(request))
    organizer_busy = _commitments(request.commitments, request.zone)
    shift_busy, _, shift_ambiguities, shift_uncertainties = _shifts(request.shifts, request.zone)
    if shift_ambiguities:
        return {'ambiguities': shift_ambiguities, 'candidates': [], 'common_windows': [], 'availability_scope': 'incomplete'}
    overlap = _subtract(overlap or [], [(x['start'], x['end']) for x in organizer_busy+shift_busy])
    candidates = []
    duration = timedelta(minutes=request.durationMinutes)
    for start, end in overlap or []:
        if end-start < duration:
            continue
        if len(candidates) == 20:
            break
        candidate = _serialized([(start, start+duration)], request.zone)[0]
        candidate['participant_times'] = {p.id: _serialized([(start, start+duration)], p.zone)[0] for p in request.participants}
        candidates.append(candidate)
    return {'ambiguities': [], 'common_windows': _serialized(overlap or [], request.zone), 'candidates': candidates,
            'uncertainties': shift_uncertainties,
            'availability_scope': 'Only the supplied explicit windows and busy intervals',
            'candidate_policy': 'Earliest fit in each common window, up to 20; no invitations or calendar holds'}


def _agenda(request):
    if request.agenda is None:
        return {'ambiguities': [{'field': 'agenda', 'code': 'agenda_input_required'}], 'items': []}
    agenda = request.agenda
    offset = 0
    requested_minutes = sum(x.minutes for x in agenda.items if x.minutes is not None)
    items, deferred, ambiguities = [], [], []
    remaining = list(agenda.items)
    processed, admitted = set(), set()
    while remaining:
        ready = next((x for x in remaining if set(x.dependsOn) <= processed), None)
        if ready is None:
            ambiguities.append({'field': 'agenda.items', 'code': 'dependency_cycle'})
            deferred.extend(x.model_dump() | {'reason': 'dependency_cycle', 'start_minute': None, 'end_minute': None} for x in remaining)
            break
        remaining.remove(ready)
        processed.add(ready.id)
        reason = None
        if ready.minutes is None:
            ambiguities.append({'field': 'agenda.items.'+ready.id+'.minutes', 'code': 'duration_required'})
            reason = 'duration_required'
        elif not set(ready.dependsOn) <= admitted:
            reason = 'dependency_not_admitted'
        elif agenda.durationMinutes is None:
            reason = 'meeting_duration_required'
        elif offset + ready.minutes > agenda.durationMinutes:
            reason = 'insufficient_remaining_capacity'
        if reason:
            deferred.append(ready.model_dump() | {'reason': reason, 'start_minute': None, 'end_minute': None})
            continue
        items.append(ready.model_dump() | {'start_minute': offset, 'end_minute': offset+ready.minutes})
        offset += ready.minutes
        admitted.add(ready.id)
    if not agenda.items:
        ambiguities.append({'field': 'agenda.items', 'code': 'agenda_items_required'})
    if agenda.durationMinutes is None:
        ambiguities.append({'field': 'agenda.durationMinutes', 'code': 'meeting_duration_required'})
    overflow = max(0, requested_minutes-agenda.durationMinutes) if agenda.durationMinutes is not None else None
    return {'title': agenda.title, 'purpose': agenda.purpose, 'items': items, 'deferred': deferred,
            'requested_items': [x.model_dump() for x in agenda.items], 'ambiguities': ambiguities,
            'known_minutes': requested_minutes, 'allocated_minutes': offset,
            'capacity_minutes': agenda.durationMinutes, 'overflow_minutes': overflow,
            'unallocated_minutes': agenda.durationMinutes-offset if agenda.durationMinutes is not None else None,
            'scope': 'Whole topics only; stable dependency order then supplied order. Deferred topics are retained unchanged. Owners are supplied labels, not invitations.'}


def _timezone(request):
    values, ambiguities = [], []
    if not request.times:
        ambiguities.append({'field': 'times', 'code': 'explicit_time_values_required'})
    zones = request.displayZones or [request.zone]
    for zone in zones:
        zone_data(zone)
    for index, source in enumerate(request.times):
        try:
            resolved = normalize_time(source, default_zone=request.zone)
            displays = []
            for zone in zones:
                if resolved['kind'] == 'date':
                    displays.append({'zone': zone, 'date': resolved['date'], 'utc': None,
                                     'notice': 'Date-only is preserved, not converted as a midnight instant'})
                else:
                    displays.append(normalize_time({'kind': 'instant', 'value': resolved['utc'], 'zone': zone}))
            values.append({'index': index, 'source': source.model_dump(), 'normalized': resolved, 'displays': displays})
        except TimeResolutionError as exc:
            ambiguities.append({'field': 'times.'+str(index), 'code': exc.code, 'candidates': exc.candidates})
    return {'normalized_times': values, 'ambiguities': ambiguities,
            'scope': 'Explicit date/instant conversion only; no calendar event or recurrence was created'}


def execute_planning(request: PlanningRequest | dict) -> dict:
    request = request if isinstance(request, PlanningRequest) else PlanningRequest.model_validate(request)
    base = {'operation': request.operation, 'proposal_only': True, 'events_created': 0, 'reminders_created': 0,
            'messages_sent': 0, 'zone': request.zone, 'algorithm': 'bounded-greedy-v1',
            'limitations': ['No automatic calendar mutation or global optimum claim; feasibility uses supplied constraints only.'],
            'assumptions': ['Optional event buffers default to no additional declared buffer. Unspecified real travel/rest is not established by this plan; verify before booking.']}
    try:
        base['timezone_data'] = zone_data(request.zone)
        if request.startDate:
            parse_date(request.startDate)
        if request.operation == 'timezone':
            result = _timezone(request)
        elif request.operation == 'agenda':
            result = _agenda(request)
        elif request.operation == 'meeting-search':
            result = _meeting(request)
        elif request.operation == 'replan':
            if request.scenario is None:
                result = {'ambiguities': [{'field': 'scenario', 'code': 'explicit_contingency_required'}]}
            else:
                baseline = _planning(request)
                extra = _commitments(request.scenario.addedCommitments, request.zone)
                extra += [{'id': x.id, 'title': request.scenario.reason, 'kind': 'contingency',
                           'start': (r := _bounds(x, request.zone))[0], 'end': r[1], 'raw_start': r[0], 'raw_end': r[1]} for x in request.scenario.unavailable]
                result = _planning(request, extra=extra)
                before = {x['task_id']: x['blocks'] for x in baseline['scheduled']}
                after = {x['task_id']: x['blocks'] for x in result['scheduled']}
                result['changes'] = [{'task_id': key, 'before': before.get(key), 'after': after.get(key)} for key in sorted(set(before)|set(after)) if before.get(key) != after.get(key)]
                result['reason'] = request.scenario.reason
                result['baseline_metrics'] = baseline['metrics']
        else:
            result = _planning(request)
            if request.operation == 'shift' and not request.shifts:
                result['ambiguities'].append({'field': 'shifts', 'code': 'explicit_shift_intervals_required'})
        unresolved = bool(result.get('ambiguities'))
        incomplete = bool(result.get('deferred') or result.get('conflicts', {}).get('items') or result.get('overflow_minutes') or result.get('uncertainties'))
        return base | result | {'status': 'needs_input' if unresolved else 'partial' if incomplete else 'proposed'}
    except TimeResolutionError as exc:
        return base | {'status': 'needs_input', 'ambiguities': [{'field': 'time', 'code': exc.code, 'candidates': exc.candidates}],
                       'scheduled': [], 'deferred': [], 'free_windows': [], 'conflicts': {'items': []}}
    except OverflowError:
        return base | {'status': 'needs_input', 'ambiguities': [{'field': 'time', 'code': 'date_out_of_range'}],
                       'scheduled': [], 'deferred': [], 'free_windows': [], 'conflicts': {'items': []}}
