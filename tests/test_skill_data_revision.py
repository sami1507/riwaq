"""Deterministic contracts and bounded orchestration; Docker proof is separate."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import json
from io import BytesIO
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pytest
from pydantic import ValidationError
from backend.skill_core import SkillInputs
from backend import skill_data_revision as revision
from backend.skill_data import execute_data, validate_request
from backend.skill_transform_protocol import TransformResult, Artifact

TABLE={'columns':['order','amount'],'rows':[['A',0],['B',10],['C',20],['D',None]]}
REPORT={'title':'تقرير القيم','language':'ar','chart':{'category':'order','value':'amount','unit':'وحدة'}}


def inputs(**changes):
    value={'dataOperation':{'operation':'explore','table':TABLE,'columns':['amount']},'dataReport':REPORT}
    value.update(changes)
    return SkillInputs.model_validate(value)


@pytest.mark.parametrize('bad',[True,{},[],float('inf')])
def test_correction_rejects_non_numeric_structured_or_nonfinite_values(bad):
    # Booleans are valid general table scalars, but not valid report quantities.
    if bad is True:
        with pytest.raises(ValueError):revision._bounded_table({'columns':['order','amount'],'rows':[['a',bad]]},revision.DataReport.model_validate(REPORT),['amount'])
    else:
        with pytest.raises(ValueError):revision.CellCorrection(cell='B2',expected=0,value=bad)


def test_edit_requires_expected_and_cannot_claim_authority_or_duplicate():
    with pytest.raises(ValidationError):revision.CellCorrection(cell='B2',value=1)
    with pytest.raises(ValidationError):revision.CellCorrection(cell='B2',expected=0,value=1,verified=True)
    with pytest.raises(ValidationError):revision.DataRevision(expectedResultHash='0'*64,edits=[{'cell':'B2','expected':0,'value':1}]*2)


@pytest.mark.parametrize('change',[{'filters':[{'column':'amount','operator':'ne','value':0}]},{'weight_column':'amount'},{'columns':[]}])
def test_report_rejects_silent_population_or_denominator_changes(change):
    value=inputs();value.dataOperation.update(change)
    with pytest.raises(ValueError):revision.validate_plan(['exploratory-analysis'],value)


def test_unknown_tool_field_is_rejected_not_silently_ignored():
    # Actual pinned engine contract: series is plural `values`, unlike the
    # deliberately single-series public report contract's `value`.
    with pytest.raises(ValueError):
        validate_request({'operation':'chart','table':TABLE,'chart':{'kind':'bar','category':'order','value':'amount'}})


def local_transport(monkeypatch, *, fail_chart=False):
    from backend import db,skill_transform,skill_operations
    calls=[]
    @contextmanager
    def empty_db():yield None
    monkeypatch.setattr(db,'get_db',empty_db)
    def transform(domain,request,sources,**kwargs):
        calls.append(request)
        if fail_chart and request['operation']=='chart':raise ValueError('synthetic chart failure')
        template=next((x['content'] for x in sources if x.get('role')=='template'),None)
        result=execute_data(request,template_bytes=template)
        artifact=result.artifact
        output={key:value for key,value in asdict(result).items() if key!='artifact'}
        return TransformResult(output,() if artifact is None else (Artifact(**asdict(artifact)),),(),{'test_mode':'component_transport_simulation'})
    async def stopped(fn,check):return fn(None)
    monkeypatch.setattr(skill_transform,'execute_transform',transform)
    monkeypatch.setattr(skill_operations,'stopped_thread',stopped)
    return calls


def test_linked_outputs_share_values_and_preserve_zero_missing(monkeypatch):
    calls=local_transport(monkeypatch)
    result,items,partial,text=asyncio.run(revision.run(inputs(),None,'p','r',lambda:None))
    assert not partial
    stats=result['output']['summary']['statistics']['amount']
    assert (stats['sum'],stats['mean'],stats['numeric_count'],stats['missing'])==('30','10',3,1)
    assert len(items)==3 and set(x['name'] for x in items)==set(revision.NAMES)
    assert json.loads(items[0]['content'])['table']['rows']==TABLE['rows']
    assert json.loads(items[0]['content'])['summary']['statistics']['amount']==stats
    assert calls[-1]['chart']['values']==['amount'] and 'value' not in calls[-1]['chart']
    with ZipFile(BytesIO(items[1]['content'])) as z:
        chart=ET.fromstring(z.read('xl/charts/chart1.xml'))
        ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}
        assert [x.text for x in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v',ns)]==['0','10','20']
        assert chart.find('.//c:dispBlanksAs',ns).get('val')=='gap'
    with ZipFile(BytesIO(items[2]['content'])) as z:
        doc=z.read('word/document.xml').decode()
        assert 'amount' in doc and 'المجموع: 30' in doc and 'المتوسط: 10' in doc
        assert '<w:tbl>' in doc and 'w:bidi' in doc
    assert result['data_report']['native_office_rendered'] is False


def test_any_downstream_failure_returns_no_publishable_bundle(monkeypatch):
    calls=local_transport(monkeypatch,fail_chart=True)
    with pytest.raises(ValueError):asyncio.run(revision.run(inputs(),None,'p','r',lambda:None))
    assert [x['operation'] for x in calls]==['explore','chart']


def test_record_limit_does_not_truncate_report_rows():
    with pytest.raises(ValueError):revision._bounded_table({'columns':['order','amount'],'rows':[['A',0]]*101},revision.DataReport.model_validate(REPORT),['amount'])


def test_numeric_text_is_not_silently_excluded():
    with pytest.raises(ValueError):revision._bounded_table({'columns':['order','amount'],'rows':[['A','0']]},revision.DataReport.model_validate(REPORT),['amount'])


def test_actual_source_package_metadata_is_not_a_report_cell():
    summary={'statistics':{'amount':{'sum':'30','mean':'10','numeric_count':3,'missing':1}},
             'package_metadata':{'diagnostic':'x'*5000}}
    tables=revision.report_tables(TABLE,summary,'ar')
    assert len(tables)==2 and tables[1]['rows'][0]==['"A"','0']
    assert 'package_metadata' not in json.dumps(tables)


@pytest.mark.parametrize('unit',[None,'','   '])
def test_missing_unit_rejected_before_starting_worker_or_inventing_unit(unit):
    chart={'category':'order','value':'amount'}
    if unit is not None:chart['unit']=unit
    with pytest.raises(ValidationError):inputs(dataReport={**REPORT,'chart':chart})


def test_coverage_requires_saved_bundle_hashes_and_actual_worker_receipts(monkeypatch):
    from copy import deepcopy
    from backend.request_contract import assess_output
    local_transport(monkeypatch)
    record,items,_,text=asyncio.run(revision.run(inputs(),None,'p','r',lambda:None))
    document={'steps':[{'skill':'exploratory-analysis','status':'completed'}],
              'result':{'structured':{'exploratory-analysis':record},'text':text,
                        'artifacts':[{k:v for k,v in x.items() if k!='content'} for x in items]}}
    checked={name:True for name in revision.NAMES}
    request={'deliverables':['analysis','summary','chart','file'],'constraints':{}}
    # Simulation labels cannot pass the actual-worker receipt requirement.
    assert not assess_output(request,document,checked)['checked_deliverables']['chart']
    # Contract fixtures below are explicitly simulated receipt metadata, not a
    # live claim. Real receipts are separately tested through the actual API.
    for step in record['data_report']['execution_receipts']:
        step['receipt']={'status':'completed','exit_code':0,'cleanup_verified':True,'policy_inspected':True}
    assert all(assess_output(request,document,checked)['checked_deliverables'].values())
    missing=dict(checked);missing['data-chart.xlsx']=False
    assert not assess_output(request,document,missing)['checked_deliverables']['chart']
    forged=deepcopy(document);forged['result']['text']='A stale summary'
    assert not assess_output(request,forged,checked)['checked_deliverables']['summary']
