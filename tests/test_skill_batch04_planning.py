"""Synthetic deterministic acceptance; real installed IANA rules, no mocked clocks/services."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from backend.productivity_time import (
    TimeInput, TimeResolutionError, RecurrenceRule, get_zone, zone_data, resolve_local,
    normalize_time, normalize_interval, local_recurrence, local_daily_occurrences,
    next_occurrence, parse_instant, relative_date,
)
from backend.productivity_planning import PlanningRequest, execute_planning


def point(clock, day='2026-11-16', zone='UTC'):
    return {'kind': 'local', 'value': day+'T'+clock, 'zone': zone}


def window(identifier, start, end, day='2026-11-16', zone='UTC'):
    return {'id': identifier, 'start': point(start, day, zone), 'end': point(end, day, zone)}


def reference():
    return {'operation': 'daily', 'zone': 'UTC', 'windows': [window('w', '09:00', '12:00')],
            'commitments': [window('m', '10:00', '11:00') | {'title': 'اجتماع שלום meeting', 'bufferBeforeMinutes': 15, 'bufferAfterMinutes': 15}],
            'tasks': [{'id': 'a', 'title': 'مهمة أولى', 'minutes': 45}, {'id': 'b', 'title': 'משימה שנייה', 'minutes': 45}, {'id': 'c', 'title': 'Third task', 'minutes': 15}]}


def test_pinned_iana_metadata_and_zone_object_agree():
    data = zone_data('America/New_York')
    assert data == {'source': 'tzdata package', 'package_version': '2026.6', 'iana_version': '2026f'}
    assert get_zone('America/New_York').key == 'America/New_York'


def test_date_is_not_midnight_instant_and_all_day_end_is_exclusive():
    value = normalize_time({'kind': 'date', 'value': '2026-03-08', 'zone': 'America/New_York'})
    assert value['date'] == '2026-03-08' and value['utc'] is None
    interval = normalize_interval({'kind': 'date', 'value': '2026-03-08'}, {'kind': 'date', 'value': '2026-03-09'})
    assert interval['kind'] == 'all_day' and interval['start_utc'] is None and interval['end_date_exclusive'] == '2026-03-09'


def test_explicit_offset_is_absolute_and_display_zone_does_not_change_instant():
    value = normalize_time({'kind': 'instant', 'value': '2026-11-16T10:00:00+02:00', 'zone': 'America/New_York'})
    assert value['utc'] == '2026-11-16T08:00:00Z' and value['local'] == '2026-11-16T03:00:00'


@pytest.mark.parametrize('value', [
    {'kind': 'local', 'value': '2026-11-16T09:00'},
    {'kind': 'instant', 'value': '2026-11-16T09:00'},
    {'kind': 'date', 'value': '11/12/2026'},
    {'kind': 'local', 'value': '2026-02-30T09:00', 'zone': 'UTC'},
    {'kind': 'local', 'value': '2026-11-16T24:00', 'zone': 'UTC'},
    {'kind': 'local', 'value': '2026-11-16T09:00', 'zone': '../../private'},
    {'kind': 'local', 'value': '2026-11-16T09:00', 'zone': 'Missing/NotAZone'},
])
def test_invalid_or_ambiguous_temporal_input_never_guesses(value):
    with pytest.raises(TimeResolutionError):
        normalize_time(value)


@pytest.mark.parametrize('fold', [True, False, '0', 2, -1])
def test_fold_is_explicit_integer_choice(fold):
    with pytest.raises(ValueError):
        TimeInput.model_validate({'kind': 'local', 'value': '2026-11-01T01:30', 'zone': 'America/New_York', 'fold': fold})


def test_dst_gap_and_fold_are_explicit_and_differ_by_real_hour():
    with pytest.raises(TimeResolutionError) as gap:
        resolve_local('2026-03-08T02:30', 'America/New_York')
    assert gap.value.code == 'nonexistent_local_time'
    with pytest.raises(TimeResolutionError) as fold:
        resolve_local('2026-11-01T01:30', 'America/New_York')
    assert fold.value.code == 'ambiguous_local_time' and len(fold.value.candidates) == 2
    early = resolve_local('2026-11-01T01:30', 'America/New_York', 0)
    late = resolve_local('2026-11-01T01:30', 'America/New_York', 1)
    assert late-early == timedelta(hours=1)


def test_elapsed_duration_uses_utc_across_fold():
    interval = normalize_interval(point('01:30', '2026-11-01', 'America/New_York') | {'fold': 0},
                                  point('01:45', '2026-11-01', 'America/New_York') | {'fold': 1})
    assert interval['elapsed_seconds'] == 75*60


@pytest.mark.parametrize('left,right', [
    (point('10:00'), point('10:00')), (point('11:00'), point('10:00')),
    ({'kind': 'date', 'value': '2026-11-16'}, point('10:00')),
])
def test_invalid_interval_has_no_normalized_success(left, right):
    with pytest.raises(TimeResolutionError):
        normalize_interval(left, right)


def test_daily_recurrence_preserves_wall_clock_and_23_25_hour_intervals():
    spring = local_daily_occurrences('2026-03-07T09:00', 'America/New_York', count=3)
    fall = local_daily_occurrences('2026-10-31T09:00', 'America/New_York', count=3)
    assert [x['local'][-8:] for x in spring['occurrences']] == ['09:00:00']*3
    assert parse_instant(spring['occurrences'][1]['utc'])-parse_instant(spring['occurrences'][0]['utc']) == timedelta(hours=23)
    assert parse_instant(fall['occurrences'][1]['utc'])-parse_instant(fall['occurrences'][0]['utc']) == timedelta(hours=25)


def test_recurrence_gap_rejects_or_explicitly_skips_without_shifting():
    with pytest.raises(TimeResolutionError):
        local_daily_occurrences('2026-03-07T02:30', 'America/New_York', count=2)
    value = local_daily_occurrences('2026-03-07T02:30', 'America/New_York', count=2, nonexistent='skip')
    assert len(value['skipped']) == 1
    assert [x['local'] for x in value['occurrences']] == ['2026-03-07T02:30:00', '2026-03-09T02:30:00']


def test_weekly_recurrence_is_anchored_and_next_cursor_is_strict():
    rule = {'frequency': 'weekly', 'weekdays': [0, 4], 'count': 3}
    values = local_recurrence('2026-11-18T09:00', 'UTC', rule)['occurrences']
    assert [x['local'][:10] for x in values] == ['2026-11-20', '2026-11-23', '2026-11-27']
    assert next_occurrence('2026-11-18T09:00', 'UTC', rule, values[0]['utc']) == values[1]
    assert next_occurrence('2026-11-18T09:00', 'UTC', rule, values[-1]['utc']) is None


def test_recurrence_until_is_inclusive_and_bounded():
    values = local_daily_occurrences('2026-11-16T09:00', 'UTC', count=50, until_local='2026-11-17T09:00')
    assert len(values['occurrences']) == 2
    with pytest.raises(ValueError):
        RecurrenceRule.model_validate({'count': 367})
    with pytest.raises(ValueError):
        RecurrenceRule.model_validate({'frequency': 'weekly', 'weekdays': [0, 0]})


@pytest.mark.parametrize('expression', ['tomorrow', 'بكرا', 'غدًا', 'מחר'])
def test_relative_tomorrow_uses_explicit_reference_not_system_clock(expression):
    result = relative_date(expression, '2026-11-16', 'Asia/Jerusalem')
    assert result['date'] == '2026-11-17' and result['utc'] is None


def test_next_week_requires_chosen_week_boundary_and_unknown_stays_ambiguous():
    assert relative_date('next week', '2026-11-16', 'UTC')['reason'] == 'week_start_required'
    assert relative_date('الأسبوع الجاي', '2026-11-16', 'UTC', 6)['start_date'] == '2026-11-22'
    assert relative_date('שבוע הבא', '2026-11-16', 'UTC', 0)['start_date'] == '2026-11-23'
    assert relative_date('tomorrow', None, 'UTC')['reason'] == 'reference_date_required'
    assert relative_date('laterish', '2026-11-16', 'UTC')['kind'] == 'ambiguous'


def test_reference_day_exact_capacity_and_labels_no_side_effects():
    request = reference(); original = deepcopy(request)
    result = execute_planning(request)
    assert request == original
    assert result['status'] == 'partial'
    assert result['metrics']['available_before_tasks_minutes'] == 90
    assert result['metrics']['scheduled_minutes'] == 90 and result['metrics']['deferred_known_minutes'] == 15
    assert [x['title'] for x in result['scheduled']] == ['مهمة أولى', 'משימה שנייה']
    assert result['scheduled'][0]['blocks'][0]['end_utc'] == '2026-11-16T09:45:00Z'
    assert result['scheduled'][1]['blocks'][0]['start_utc'] == '2026-11-16T11:15:00Z'
    assert result['deferred'][0]['task_id'] == 'c'
    assert result['events_created'] == result['reminders_created'] == result['messages_sent'] == 0


def test_contiguous_60_cannot_fit_two_45_but_explicit_split_can():
    request = reference(); request['tasks'] = [{'id': 'a', 'title': 'One hour', 'minutes': 60}]
    result = execute_planning(request)
    assert result['scheduled'] == [] and result['deferred'][0]['reason'] == 'contiguous_slot_unavailable'
    request['tasks'][0]['splittable'] = True
    split = execute_planning(request)
    assert [x['minutes'] for x in split['scheduled'][0]['blocks']] == [45, 15]
    assert split['metrics']['remaining_free_minutes'] == 30


def test_zero_is_milestone_unknown_is_deferred_no_implicit_duration():
    request = reference(); request['tasks'] = [{'id': 'zero', 'title': 'Milestone', 'minutes': 0}, {'id': 'missing', 'title': 'Unknown'}]
    value = execute_planning(request)
    assert value['status'] == 'needs_input'
    assert value['scheduled'][0]['kind'] == 'milestone' and value['scheduled'][0]['minutes'] == 0
    assert value['deferred'][0]['minutes'] is None and value['metrics']['remaining_free_minutes'] == 90


def test_overlapping_availability_is_not_double_counted():
    request = {'operation': 'daily', 'zone': 'UTC', 'windows': [window('a', '09:00', '11:00'), window('b', '10:00', '12:00')]}
    assert execute_planning(request)['metrics']['availability_minutes'] == 180


def test_dependency_order_and_deadline_are_applied_before_allocation():
    request = reference(); request['commitments'] = []
    request['tasks'] = [{'id': 'later', 'title': 'Dependent', 'minutes': 30, 'priority': 100, 'dependsOn': ['first']},
                        {'id': 'first', 'title': 'Prerequisite', 'minutes': 30, 'earliest': point('10:00')}]
    result = execute_planning(request)
    assert [x['task_id'] for x in result['scheduled']] == ['first', 'later']
    assert result['scheduled'][1]['blocks'][0]['start_utc'] == '2026-11-16T10:30:00Z'
    request['tasks'][0]['deadline'] = point('10:45')
    result = execute_planning(request)
    assert result['deferred'][0]['task_id'] == 'later'


def test_unknown_dependency_duration_blocks_dependent_and_cycle_is_not_executed():
    request = reference(); request['tasks'] = [{'id': 'a', 'title': 'Unknown'}, {'id': 'b', 'title': 'Dependent', 'minutes': 30, 'dependsOn': ['a']}]
    result = execute_planning(request)
    assert [x['reason'] for x in result['deferred']] == ['duration_unknown', 'dependency_not_scheduled']
    request['tasks'][0]['dependsOn'] = ['b']
    result = execute_planning(request)
    assert result['status'] == 'needs_input' and result['ambiguities'][0]['code'] == 'cyclic_task_dependencies'


def test_weekly_start_date_and_actual_supplied_days_only():
    request = {'operation': 'weekly', 'zone': 'UTC', 'windows': [window('monday', '09:00', '10:00'), window('sunday', '09:00', '10:00', '2026-11-22')],
               'tasks': [{'id': 'a', 'title': 'A', 'minutes': 90}]}
    assert execute_planning(request)['status'] == 'needs_input'
    request |= {'startDate': '2026-11-16', 'weekStartsOn': 0}
    result = execute_planning(request)
    assert result['metrics']['availability_minutes'] == 120 and result['scheduled'] == []
    request['tasks'][0]['splittable'] = True
    assert execute_planning(request)['metrics']['scheduled_minutes'] == 90


def test_daily_two_dates_without_target_date_requires_clarification():
    request = {'operation': 'daily', 'zone': 'UTC', 'windows': [window('one', '09:00', '10:00'), window('two', '09:00', '10:00', '2026-11-17')]}
    assert execute_planning(request)['status'] == 'needs_input'


def test_shift_cross_midnight_requires_explicit_next_day_and_rest_is_per_shift():
    shift = {'id': 'night', 'title': 'مناوبة', 'startDate': '2026-11-16', 'startTime': '18:00', 'endTime': '01:00',
             'commuteBeforeMinutes': 15, 'commuteAfterMinutes': 30, 'restAfterMinutes': 480}
    request = {'operation': 'shift', 'zone': 'UTC', 'shifts': [shift]}
    assert execute_planning(request)['ambiguities'][0]['code'] == 'overnight_next_day_required'
    shift['endNextDay'] = True
    value = execute_planning(request)
    assert value['shifts'][0]['work']['minutes'] == 420
    assert value['shifts'][0]['protected_until_utc'] == '2026-11-17T09:30:00Z'
    request['shifts'].append({'id': 'early', 'startDate': '2026-11-17', 'startTime': '08:00', 'endTime': '09:00', 'restAfterMinutes': 0})
    assert execute_planning(request)['conflicts']['items'][0]['kind'] == 'buffer_or_rest_overlap'


def test_shift_can_cross_clock_fold_without_crossing_date_when_explicit():
    result = execute_planning({'operation': 'shift', 'zone': 'America/New_York', 'shifts': [
        {'id': 'fold', 'startDate': '2026-11-01', 'startTime': '01:45', 'endTime': '01:15', 'endNextDay': False, 'foldStart': 0, 'foldEnd': 1,
         'commuteBeforeMinutes': 0, 'commuteAfterMinutes': 0, 'restAfterMinutes': 0}]})
    assert result['status'] == 'proposed' and result['shifts'][0]['work']['minutes'] == 30


def test_conflict_half_open_edges_and_buffers_distinct():
    request = {'operation': 'conflicts', 'zone': 'UTC', 'commitments': [window('a', '09:00', '10:00'), window('b', '10:00', '11:00')]}
    assert execute_planning(request)['conflicts']['items'] == []
    request['commitments'][0]['bufferAfterMinutes'] = 15
    conflict = execute_planning(request)['conflicts']['items'][0]
    assert conflict['kind'] == 'buffer_or_rest_overlap' and conflict['minutes'] == 15


def test_all_day_commitment_blocks_working_hours_without_claiming_midnight_source():
    request = reference(); request['commitments'] = [{'id': 'day', 'start': {'kind': 'date', 'value': '2026-11-16'}, 'end': {'kind': 'date', 'value': '2026-11-17'}}]
    result = execute_planning(request)
    assert result['metrics']['available_before_tasks_minutes'] == 0 and len(result['deferred']) == 3
    request['windows'] = [request['commitments'][0]]
    assert execute_planning(request)['ambiguities'][0]['code'] == 'working_hours_required_for_date'


def meeting():
    return {'operation': 'meeting-search', 'zone': 'UTC', 'durationMinutes': 30, 'participants': [
        {'id': 'a', 'zone': 'UTC', 'label': 'عربي', 'windows': [window('a', '09:00', '11:00')]},
        {'id': 'b', 'zone': 'UTC', 'label': 'עברית', 'windows': [window('b', '10:00', '12:00')]},
        {'id': 'c', 'zone': 'UTC', 'label': 'English', 'windows': [window('c', '09:30', '10:30')]}]}


def test_three_person_intersection_actual_utc_not_summed_availability():
    result = execute_planning(meeting())
    assert result['status'] == 'proposed' and len(result['candidates']) == 1
    assert result['candidates'][0]['start_utc'] == '2026-11-16T10:00:00Z'
    assert result['candidates'][0]['end_utc'] == '2026-11-16T10:30:00Z'
    assert set(result['candidates'][0]['participant_times']) == {'a', 'b', 'c'}


def test_meeting_unknown_calendar_and_known_empty_are_different():
    request = meeting(); request['participants'][0]['known'] = False
    result = execute_planning(request)
    assert result['status'] == 'needs_input' and result['candidates'] == []
    request['participants'][0]['known'] = True
    request['participants'][0]['windows'] = []
    result = execute_planning(request)
    assert result['status'] == 'proposed' and result['candidates'] == [] and result['ambiguities'] == []


def test_meeting_uses_participant_zone_and_global_busy_constraints():
    request = meeting()
    request['participants'][1]['zone'] = 'Asia/Jerusalem'
    request['participants'][1]['windows'] = [window('b', '12:00', '14:00', zone='Asia/Jerusalem')]
    assert execute_planning(request)['candidates'][0]['start_utc'] == '2026-11-16T10:00:00Z'
    request['commitments'] = [window('organizer-busy', '10:00', '10:30')]
    assert execute_planning(request)['candidates'] == []


def test_contingency_replans_only_remaining_tasks_with_change_evidence():
    request = reference(); request['operation'] = 'replan'
    request['tasks'][2]['completed'] = True
    request['scenario'] = {'reason': 'انقطاع محلي', 'unavailable': [window('loss', '09:00', '09:45')]}
    result = execute_planning(request)
    assert result['metrics']['scheduled_minutes'] == 45
    assert [x['task_id'] for x in result['deferred']] == ['b']
    assert result['completed_tasks_unchanged'][0]['id'] == 'c'
    assert {x['task_id'] for x in result['changes']} == {'a', 'b'}
    assert result['events_created'] == 0


def test_agenda_capacity_missing_durations_and_owners_are_not_invented():
    request = {'operation': 'agenda', 'zone': 'UTC', 'agenda': {'title': 'جدول שלום agenda', 'durationMinutes': 30, 'items': [
        {'id': 'a', 'title': 'Review 001', 'minutes': 15}, {'id': 'b', 'title': 'Decision', 'minutes': 20, 'owner': 'Sam'}]}}
    result = execute_planning(request)
    assert result['status'] == 'partial' and result['overflow_minutes'] == 5
    assert result['items'][0]['owner'] is None and len(result['items']) == 1
    assert result['deferred'][0]['id'] == 'b' and result['deferred'][0]['start_minute'] is None
    request['agenda']['items'][0]['minutes'] = None
    result = execute_planning(request)
    assert result['status'] == 'needs_input' and result['deferred'][0]['start_minute'] is None
    assert result['items'][0]['id'] == 'b' and result['allocated_minutes'] == 20


@pytest.mark.parametrize('patch', [
    {'zone': None}, {'zone': ''}, {'tasks': [{'id': 'a', 'title': 'x', 'minutes': True}]},
    {'tasks': [{'id': 'a', 'title': 'x', 'minutes': -1}]}, {'durationMinutes': 0},
    {'tasks': [{'id': 'a', 'title': 'x', 'dependsOn': ['missing']}]}, {'weekStartsOn': 7},
    {'unsafe_shell': 'anything'}, {'windows': [window('same', '09:00', '10:00')]*2},
])
def test_strict_bounded_schema_rejects_unsupported_input(patch):
    with pytest.raises(ValueError):
        PlanningRequest.model_validate(reference() | patch)


def test_planning_serializes_to_json_without_authority_or_process_objects():
    assert json.loads(json.dumps(execute_planning(reference()), ensure_ascii=False))['proposal_only'] is True


def test_impossible_dependent_deadline_preserves_independent_feasible_work():
    request = reference(); request['commitments'] = []
    request['tasks'] = [{'id': 'first', 'title': 'First', 'minutes': 60},
                        {'id': 'late', 'title': 'Too late', 'minutes': 15, 'dependsOn': ['first'], 'deadline': point('09:30')},
                        {'id': 'other', 'title': 'Other', 'minutes': 30}]
    result = execute_planning(request)
    assert [x['task_id'] for x in result['scheduled']] == ['first', 'other']
    assert result['deferred'][0]['reason'] == 'deadline_before_earliest_or_dependency'


def test_shift_unknown_buffers_remain_unknown_after_canonicalization():
    request = {'operation': 'shift', 'zone': 'UTC', 'shifts': [{'id': 's', 'startDate': '2026-11-16', 'startTime': '09:00', 'endTime': '17:00'}]}
    canonical = PlanningRequest.model_validate(request).model_dump()
    result = execute_planning(canonical)
    assert result['status'] == 'partial'
    assert result['shifts'][0]['rest_after_minutes'] is None
    assert set(result['uncertainties'][0]['fields']) == {'commuteBeforeMinutes', 'commuteAfterMinutes', 'restAfterMinutes'}
    request['shifts'][0] |= {'commuteBeforeMinutes': 0, 'commuteAfterMinutes': 0, 'restAfterMinutes': 0}
    assert execute_planning(request)['status'] == 'proposed'


def test_timezone_operation_localizes_gap_without_discarding_valid_date():
    result = execute_planning({'operation': 'timezone', 'zone': 'America/New_York', 'times': [
        {'kind': 'date', 'value': '2026-03-08'}, {'kind': 'local', 'value': '2026-03-08T02:30'}],
        'displayZones': ['Asia/Jerusalem', 'UTC']})
    assert result['status'] == 'needs_input' and len(result['normalized_times']) == 1
    assert result['normalized_times'][0]['displays'][0]['date'] == '2026-03-08'
    assert result['normalized_times'][0]['displays'][0]['utc'] is None
    assert result['ambiguities'][0]['field'] == 'times.1'
    assert result['ambiguities'][0]['code'] == 'nonexistent_local_time'


def test_timezone_operation_known_fold_has_same_instant_in_two_zones():
    result = execute_planning({'operation': 'timezone', 'zone': 'America/New_York', 'times': [
        {'kind': 'local', 'value': '2026-11-01T01:30', 'fold': 1}], 'displayZones': ['UTC', 'Asia/Jerusalem']})
    assert result['status'] == 'proposed'
    displays = result['normalized_times'][0]['displays']
    assert {x['utc'] for x in displays} == {'2026-11-01T06:30:00Z'}
    assert displays[1]['local'] == '2026-11-01T08:30:00'


def test_completed_unknown_duration_does_not_become_new_work():
    request = reference(); request['tasks'] = [{'id': 'done', 'title': 'Already completed', 'completed': True},
                                              {'id': 'new', 'title': 'Next', 'minutes': 45, 'dependsOn': ['done']}]
    result = execute_planning(request)
    assert result['status'] == 'proposed' and result['completed_tasks_unchanged'][0]['minutes'] is None
    assert [x['task_id'] for x in result['scheduled']] == ['new']


def test_agenda_explicit_zero_capacity_is_distinct_from_missing():
    request = {'operation': 'agenda', 'zone': 'UTC', 'agenda': {'title': 'Short', 'durationMinutes': 0,
               'items': [{'id': 'mark', 'title': 'Milestone', 'minutes': 0}]}}
    result = execute_planning(request)
    assert result['status'] == 'proposed' and result['capacity_minutes'] == 0
    request['agenda']['durationMinutes'] = None
    assert execute_planning(request)['status'] == 'needs_input'


def test_planner_date_arithmetic_overflow_is_explained():
    result = execute_planning({'operation': 'weekly', 'zone': 'UTC', 'startDate': '9999-12-30'})
    assert result['status'] == 'needs_input' and result['ambiguities'][0]['code'] == 'date_out_of_range'


def test_agenda_admits_only_whole_topics_within_capacity_and_retains_deferred():
    result = execute_planning({'operation': 'agenda', 'zone': 'UTC', 'agenda': {
        'title': 'Bounded agenda', 'durationMinutes': 30, 'items': [
            {'id':'a','title':'First','minutes':15}, {'id':'b','title':'Large','minutes':20},
            {'id':'c','title':'Independent short','minutes':10}]}})
    assert sum(x['minutes'] for x in result['items']) <= 30
    assert [x['id'] for x in result['items']] == ['a','c']
    assert result['allocated_minutes'] == 25 and result['unallocated_minutes'] == 5
    assert result['deferred'][0]['id'] == 'b' and result['deferred'][0]['minutes'] == 20
    assert result['deferred'][0]['reason'] == 'insufficient_remaining_capacity'
    assert len(result['requested_items']) == 3 and result['events_created'] == 0


def test_agenda_dependency_cannot_precede_prerequisite_or_run_after_deferral():
    request = {'operation':'agenda','zone':'UTC','agenda':{'title':'Dependencies','durationMinutes':30,'items':[
        {'id':'child','title':'Decision','minutes':10,'dependsOn':['parent']},
        {'id':'parent','title':'Evidence','minutes':20}]}}
    result = execute_planning(request)
    assert [x['id'] for x in result['items']] == ['parent','child']
    request['agenda']['items'][1]['minutes'] = 40
    result = execute_planning(request)
    assert result['items'] == []
    assert next(x for x in result['deferred'] if x['id']=='child')['reason'] == 'dependency_not_admitted'


def test_agenda_cycle_and_missing_capacity_do_not_allocate_imaginary_time():
    request={'operation':'agenda','zone':'UTC','agenda':{'title':'Cycle','durationMinutes':20,'items':[
        {'id':'a','title':'أ','minutes':10,'dependsOn':['b']},{'id':'b','title':'ב','minutes':10,'dependsOn':['a']}]}}
    result=execute_planning(request)
    assert result['status']=='needs_input' and result['items']==[] and len(result['deferred'])==2
    request['agenda']['items']=[{'id':'a','title':'Unknown','minutes':None},{'id':'b','title':'Known','minutes':10,'dependsOn':['a']}]
    result=execute_planning(request)
    assert result['items']==[] and len(result['deferred'])==2
    request['agenda']['durationMinutes']=None
    request['agenda']['items']=[{'id':'b','title':'Known','minutes':10}]
    result=execute_planning(request)
    assert result['status']=='needs_input' and result['items']==[] and result['allocated_minutes']==0
