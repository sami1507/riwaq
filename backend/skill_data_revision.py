"""Version-bound data report regeneration through the existing isolated data engine.

This module orchestrates fixed operations. It neither parses uploaded packages on
the host nor publishes files: the existing skill-run transaction owns publication.
"""
from __future__ import annotations
import hashlib
import json
from typing import Any, Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend import guard

VERSION = 'data-report-v1'
NAMES = ('data-analysis.json', 'data-chart.xlsx', 'data-report.docx')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ReportChart(Strict):
    category: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=100, pattern=r'\S')


class DataReport(Strict):
    title: str = Field(min_length=1, max_length=180)
    language: Literal['ar', 'en'] = 'ar'
    chart: ReportChart


class CellCorrection(Strict):
    cell: str = Field(pattern=r'^[A-L](?:[2-9]|[1-9][0-9]|10[01])$')
    expected: Any
    value: Any

    @model_validator(mode='after')
    def scalar_values(self):
        from backend.skill_data import validate_table
        validate_table({'columns':['expected','value'], 'rows':[[self.expected,self.value]]})
        return self


class DataRevision(Strict):
    expectedResultHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    edits: list[CellCorrection] = Field(default_factory=list, max_length=100)
    # Empty edits explicitly recompute every dependent output using the saved
    # recipe; useful for correcting a calculation policy rather than input data.
    reason: str = Field(default='Recompute all dependent outputs', min_length=1, max_length=250)
    feedbackId: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,100}$')
    expectedFeedbackVersion: int | None = Field(default=None,ge=1)

    @model_validator(mode='after')
    def distinct_cells(self):
        if len({x.cell for x in self.edits}) != len(self.edits):
            raise ValueError('A cell may be corrected once per revision')
        if (self.feedbackId is None) != (self.expectedFeedbackVersion is None):
            raise ValueError('Feedback binding requires its ID and expected version')
        return self


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _item(name, content, mime):
    return {'name':name, 'content':content, 'mime':mime, 'sha256':hashlib.sha256(content).hexdigest()}


def _bundle(row):
    return json.loads(row['document']).get('result',{}).get('structured',{}).get('exploratory-analysis',{}).get('data_report')


def availability(row):
    from backend.skill_revision import result_hash
    bundle = _bundle(row)
    supported = (row['state'] in {'completed','partial'} and isinstance(bundle,dict)
                 and bundle.get('version') == VERSION)
    return {'available':supported, 'result_hash':result_hash(row),
            'table_sha256':bundle.get('table_sha256') if supported else None,
            'scope':'Saved table, statistics, editable chart workbook and editable report; original versions retained',
            'maximum_rows':100, 'maximum_columns':12}


def _parent(c, parent, expected, *, active=True, who=None):
    from backend.skill_revision import check_parent
    result = check_parent(c,parent,expected)
    bundle = _bundle(parent)
    if not availability(parent)['available']:
        raise HTTPException(422,'Choose a saved linked data report; an arbitrary narrative has no reproducible data recipe.')
    output = result['structured']['exploratory-analysis']['output']
    if guard.digest(output['table']) != bundle['table_sha256'] or guard.digest(output['summary']) != bundle['analysis_sha256']:
        raise HTTPException(409,'Saved data or analysis changed; revision was withheld.')
    if set(x['name'] for x in result['artifacts']) != set(NAMES):
        raise HTTPException(409,'The complete dependent output set is required.')
    if active:
        from backend import skill_files
        if who is None:
            raise ValueError('A current authenticated actor is required')
        for item in result['artifacts']:
            head = skill_files.head(c,who,parent['project_id'],item['artifact_id'])
            if head['latest_version'] != item['version']:
                raise HTTPException(409,'A newer dependent output version exists; refresh the report before correcting it.')
        for ref in bundle.get('source_refs',[]):
            skill_files.resolve(c,who,parent['project_id'],ref,active=True)
    return bundle, output, result


def bind(c, parent, body, who=None):
    request = body.inputs.dataRevision
    if request is None:
        return body, None
    if parent is None:
        raise HTTPException(422,'Choose the saved data report to correct.')
    if set(body.inputs.model_dump(exclude_defaults=True,exclude_none=True))-{'dataRevision'} or body.requestedSkills:
        raise HTTPException(422,'A data correction cannot include replacement tables, another tool or an output destination.')
    bundle, output, _ = _parent(c,parent,request.expectedResultHash,who=who)
    report = DataReport.model_validate(bundle['report'])
    operation = dict(bundle['analysis_request'], table=output['table'])
    binding = {'version':VERSION, 'parent_id':parent['id'], 'result_hash':request.expectedResultHash,
               'table_sha256':bundle['table_sha256'], 'recipe_sha256':guard.digest(bundle['analysis_request']),
               'original_artifacts_retained':True, 'scope':'all_declared_dependent_outputs'}
    feedback=_feedback(c,who,parent['project_id'],parent['id'],request.model_dump())
    if feedback:
        binding['feedback']={'id':feedback['id'],'version':feedback['version'],'target':feedback['target']}
    effective = body.model_copy(update={'inputs':body.inputs.model_copy(update={
        'dataOperation':operation, 'dataReport':report}), 'requestedSkills':['exploratory-analysis']})
    return effective, binding


def check_link(c, child, parent):
    spec = json.loads(child['payload']).get('inputs',{}).get('dataRevision')
    if spec:
        # A completed historical revision remains readable after a later revision.
        # Before publication all four latest heads and upstream versions must match.
        _parent(c,parent,spec['expectedResultHash'],active=child['state'] in {'queued','running','unknown'},
                who=guard.Actor(child['owner_id'],child['session_id']))
        if child['state'] in {'queued','running','unknown'}:
            _feedback(c,guard.Actor(child['owner_id'],child['session_id']),child['project_id'],parent['id'],spec)


def _feedback(c,who,pid,parent_id,spec):
    if not spec.get('feedbackId'):
        return None
    from backend import learning
    try:
        value=learning.get_feedback(c,who,pid,spec['feedbackId'])
        if value['version']!=spec['expectedFeedbackVersion'] or value.get('content_redacted') or value['status']=='deleted':
            raise HTTPException(409,'Feedback changed or is no longer available.')
        if (value['target']['kind'],value['target']['id'])!=('tool',parent_id) or value['signal'] not in {'correction','negative','operational_failure'}:
            raise HTTPException(409,'Feedback must address this exact saved tool result.')
        if 'current_task' not in value['purposes']:
            raise HTTPException(403,'Current task use is not authorized for this feedback.')
        learning.bind_target(c,who,pid,'tool',parent_id,value['target']['version'])
        return value
    except learning.LearningError as exc:
        raise HTTPException(exc.status_code,exc.code) from exc


def record_feedback(c,row,doc):
    """Called only inside the successful all-output publication transaction."""
    payload=json.loads(row['payload']);spec=payload.get('inputs',{}).get('dataRevision')
    if not spec or not spec.get('feedbackId'):
        return
    from backend import learning
    who=guard.Actor(row['owner_id'],row['session_id'])
    feedback=_feedback(c,who,row['project_id'],payload['previousRunId'],spec)
    record=doc['result']['structured']['exploratory-analysis']
    report=record['data_report']
    text=doc['result']['text']
    proof={'status':'verified','decision':'accepted','code':'dependent_data_outputs_recomputed',
           'target':feedback['target'],'objective_verified':True,'objective_error_proven':False,
           'verification_scope':'calculation_and_persisted_outputs_for_declared_input',
           'diagnosis':'input_change' if spec['edits'] else 'recompute_requested',
           'corrected_output':{'kind':'data_report','run_id':row['id'],
              'display_text_ar':text,'display_text_en':text,'table_sha256':report['table_sha256'],
              'analysis_sha256':report['analysis_sha256'],'artifacts':doc['result']['artifacts']},
           'limitations':['Cell edits are user-declared corrections, not authenticated facts.',
                          'Original answer and output versions retained; no global lesson or training.'],
           'worker_calls':len(report['execution_receipts'])}
    saved=learning.record_verification(c,who,row['project_id'],feedback['id'],feedback['version'],proof)
    doc['result']['data_revision']['feedback_result']={'id':saved['id'],'version':saved['version'],'status':saved['status']}


def output_updates(c, row):
    payload = json.loads(row['payload']); spec = payload.get('inputs',{}).get('dataRevision')
    if not spec:
        return {}
    parent = c.execute('SELECT * FROM skill_runs WHERE id=? AND owner_id=? AND project_id=?',
                       (payload['previousRunId'],row['owner_id'],row['project_id'])).fetchone()
    if not parent:
        raise HTTPException(409,'The source report is no longer available.')
    _, _, result = _parent(c,parent,spec['expectedResultHash'],who=guard.Actor(row['owner_id'],row['session_id']))
    return {item['name']:{'artifactId':item['artifact_id'], 'expectedVersion':item['version'],
                         'reason':spec.get('reason','Recompute all dependent outputs')} for item in result['artifacts']}


def validate_plan(selected, inputs):
    if not inputs.dataReport and not inputs.dataRevision:
        return
    if inputs.dataReport is None or selected != ['exploratory-analysis']:
        raise ValueError('A linked data report requires its single analysis executor')
    operation = inputs.dataOperation
    if (not operation or operation['operation'] != 'explore' or operation.get('format','json') != 'json'
        or operation.get('filters') or operation.get('forecast_review') or operation.get('weight_column')
        or operation.get('aggregate') or inputs.outputVersion or inputs.artifact or inputs.artifacts
        or inputs.documentIds or inputs.reportRevision):
        raise ValueError('A linked report uses one complete unfiltered table, explicit numeric columns and its own outputs')
    columns = operation.get('columns',[])
    if not 1 <= len(columns) <= 6 or len(set(columns)) != len(columns) or inputs.dataReport.chart.value not in columns:
        raise ValueError('Choose one to six analysis columns including the chart value')


def _bounded_table(table, report, columns):
    from backend.skill_data import validate_table
    table = validate_table(table)
    if not 1 <= len(table['rows']) <= 100 or len(table['columns']) > 12 or table['formulas']:
        raise ValueError('Linked reports support 1–100 rows and at most 12 columns of explicit values')
    if any(len(str(value))>1000 for row in table['rows'] for value in row if value is not None):
        raise ValueError('A report cell exceeds the editable document limit')
    if any(column not in table['columns'] for column in columns+[report.chart.category,report.chart.value]):
        raise ValueError('Report column is missing')
    # Numeric text, blank text and booleans must be explicitly corrected/cleaned.
    # They must not silently disappear from the denominator or turn into zero.
    for column in columns:
        index=table['columns'].index(column)
        if any(value is not None and type(value) not in (int,float) for value in (r[index] for r in table['rows'])):
            raise ValueError('Analysis values must be explicit numbers or null; clean numeric text before reporting')
    return table


def report_tables(table, summary, language):
    """Render actual values, not package/parser diagnostic blobs, into editable cells."""
    from backend.skill_artifacts import Table
    labels=['العمود','المقياس','القيمة'] if language=='ar' else ['Column','Metric','Value']
    measures=('count','numeric_count','missing','nonnumeric','sum','mean','median','min','max')
    rows=[[column,key,json.dumps(fields[key],ensure_ascii=False)]
          for column,fields in summary['statistics'].items() for key in measures if key in fields]
    return [Table(headers=labels,rows=rows).model_dump(),Table(headers=table['columns'],rows=[
        [json.dumps(v,ensure_ascii=False) for v in row] for row in table['rows']]).model_dump()]


def verified_coverage(result, steps, artifact_checks):
    """Recognize the saved report's actual outputs, never a synthetic skill step."""
    record=result.get('structured',{}).get('exploratory-analysis',{})
    bundle=record.get('data_report',{});output=record.get('output',{})
    artifacts=result.get('artifacts',[])
    if (steps.get('exploratory-analysis')!='completed' or bundle.get('version')!=VERSION
        or set(a['name'] for a in artifacts)!=set(NAMES) or len(artifacts)!=len(NAMES)
        or not all(artifact_checks.get(name) is True for name in NAMES)
        or guard.digest(output.get('table'))!=bundle.get('table_sha256')
        or guard.digest(output.get('summary'))!=bundle.get('analysis_sha256')
        or guard.digest(result.get('text'))!=bundle.get('narrative_sha256')):
        return False
    receipts=bundle.get('execution_receipts',[])
    if (not receipts or any(r.get('receipt',{}).get('status')!='completed'
            or r['receipt'].get('exit_code')!=0 or not r['receipt'].get('cleanup_verified')
            or not r['receipt'].get('policy_inspected') for r in receipts)):
        return False
    return any(c.get('native_editable_chart') and c.get('chart_series_bound_to_cells') and c.get('reopened')
               for c in record.get('artifact_checks',[]))


async def run(inputs, who, pid, rid, check):
    from backend import skill_files, skill_operations
    from backend.db import get_db
    from backend.skill_transform import execute_transform
    from backend.skill_artifacts import build_artifact
    from backend.skill_revision import analysis_narrative
    report = inputs.dataReport; request = dict(inputs.dataOperation)
    receipts=[]; checks=[]; source_refs=[]
    check()
    with get_db() as c:
        sources=[skill_files.resolve(c,who,pid,ref,active=True,writing_run_id=rid) for ref in inputs.sourceRefs]
        original_refs=[ref.model_dump() for ref in inputs.sourceRefs]
        if inputs.dataRevision:
            own=c.execute('SELECT payload FROM skill_runs WHERE id=?',(rid,)).fetchone()
            parent_id=json.loads(own['payload'])['previousRunId']
            parent=c.execute('SELECT * FROM skill_runs WHERE id=? AND owner_id=? AND project_id=?',
                             (parent_id,who.owner_id,pid)).fetchone()
            if not parent:
                raise HTTPException(409,'The source report is no longer available.')
            original_refs=_bundle(parent).get('source_refs',[])
    async def transform(operation, source_values=()):
        check()
        result=await skill_operations.stopped_thread(
            lambda stop:execute_transform('data',operation,list(source_values),check=check,stop=stop),check)
        check()
        receipts.append({'operation':operation['operation'],'receipt':result.receipt})
        source_refs.extend(result.source_refs)
        checks.extend(x.verification for x in result.artifacts)
        return result
    # The source/table is always read by the same isolated engine. A correction
    # runs against server-loaded saved data, with per-cell expected-value checks.
    if inputs.dataRevision and inputs.dataRevision.edits:
        edited=await transform({'operation':'formulas','table':request['table'],
                                'edits':[x.model_dump() for x in inputs.dataRevision.edits]})
        request['table']=edited.result['table']
    analysis=await transform(request,sources)
    output=analysis.result
    table=_bounded_table(output['table'],report,request['columns'])
    if output.get('warnings'):
        raise ValueError('Report analysis needs review; no complete output bundle was published')
    template=build_artifact('xlsx',{'title':report.title,'columns':[{'name':'Value'}],'rows':[['']]})
    chart_spec={'kind':'bar','category':report.chart.category,'values':[report.chart.value],'title':report.title,
                'unit':report.chart.unit}
    chart=await transform({'operation':'chart','table':table,'sheet_name':'Data','chart':chart_spec},[{'file_id':'trusted-template','name':'template.xlsx',
        'version':1,'sha256':template.sha256,'content':template.content,'role':'template'}])
    if chart.result.get('table') != table or len(chart.artifacts) != 1:
        raise ValueError('Chart and analysis data disagree')
    structured={'exploratory-analysis':{'output':output}}
    narrative=analysis_narrative(structured,report.language)
    document=build_artifact('docx',{'title':report.title,'language':report.language,
        'direction':'rtl' if report.language=='ar' else 'ltr',
        'paragraphs':[{'text':p} for p in narrative.split('\n\n')],
        'tables':report_tables(table,output['summary'],report.language)})
    recipe={key:request[key] for key in ('operation','columns','ddof','quantiles','record_unit') if key in request}
    recipe['format']='json'
    bundle={'version':VERSION,'report':report.model_dump(),'analysis_request':recipe,
            'table_sha256':guard.digest(table),'analysis_sha256':guard.digest(output['summary']),
            'narrative_sha256':guard.digest(narrative),
            'source_refs':original_refs,
            'outputs':list(NAMES),'missing_policy':'exclude null from numeric denominator; retain numeric zero',
            'correction_source':'user_cell_patch' if inputs.dataRevision and inputs.dataRevision.edits else 'saved_recipe_recompute' if inputs.dataRevision else 'initial_bound_data',
            'execution_receipts':receipts,'native_office_rendered':False}
    workbook=chart.artifacts[0]
    produced=[_item(NAMES[0],_dump({'table':table,'summary':output['summary'],'table_sha256':bundle['table_sha256']}),'application/json'),
              _item(NAMES[1],workbook.content,workbook.media_type),
              _item(NAMES[2],document.content,document.media_type)]
    check()
    result={'operation':'exploratory-analysis','output':output,'data_report':bundle,
            'execution_receipt':analysis.receipt,'source_refs':source_refs,'artifact_checks':checks,
            'limitations':['Native Office rendering is not established by package validation.',
                           'User cell changes are declared corrections, not independently authenticated source facts.']}
    return result,produced,False,narrative
