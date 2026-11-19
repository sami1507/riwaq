"""Verify a scoped feedback proposal against server-bound existing evidence.

The caller resolves BoundTarget/BoundSource with current authorization, then
rebinds their versions after this await before saving. This module does not write
history, memory, files, training examples, provider settings or external effects.
It performs at most two calls to the existing fixed local math checker.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
from fractions import Fraction

VERSION='learning-verification-v1'
FIELDS={
    'math_answer': ({'candidate_value'}, {'value_path'}),
    'input_correction': ({'revised_input'}, {'candidate_value','value_path','source_ref','source_quote','source_start','source_end','input_start','input_end'}),
    'source_correction': ({'source_ref','source_quote','source_start','source_end'}, set()),
    'preference': ({'text'}, set()),
    'request_change': ({'text'}, set()),
    'operational_failure': (set(), {'reason'}),
}
DIGITS=str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫−','01234567890123456789.-')


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def _text(value,limit,code):
    if type(value) is not str or not value.strip() or len(value)>limit or any(ord(c)<32 and c not in '\r\n\t' for c in value):
        raise ValueError(code)
    return value


def validate_proposal(proposal):
    if type(proposal) is not dict or proposal.get('kind') not in FIELDS:raise ValueError('invalid_correction_kind')
    required,optional=FIELDS[proposal['kind']]
    if set(proposal)-({'kind'}|required|optional) or not required<=set(proposal):raise ValueError('invalid_correction_fields')
    for name in ('candidate_value','source_quote','text','reason','revised_input'):
        if name in proposal:_text(proposal[name],12000 if name=='revised_input' else 1000 if name in ('text','reason') else 2048 if name=='source_quote' else 130,'invalid_correction_'+name)
    if 'candidate_value' in proposal:_number(proposal['candidate_value'])
    for name in ('source_start','source_end','input_start','input_end'):
        if name in proposal and (type(proposal[name]) is not int or not 0<=proposal[name]<=2_000_000):raise ValueError('invalid_correction_position')
    for begin,end in (('source_start','source_end'),('input_start','input_end')):
        if (begin in proposal)!=(end in proposal) or (begin in proposal and proposal[begin]>=proposal[end]):raise ValueError('invalid_correction_span')
    if 'source_ref' in proposal:
        ref=proposal['source_ref']
        if type(ref) is not dict or set(ref)!={'kind','id','version'} or ref['kind']!='file' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(ref['id'])) or type(ref['id']) is not str or not re.fullmatch('[a-f0-9]{64}',str(ref['version'])):
            raise ValueError('invalid_correction_source_ref')
    if 'value_path' in proposal:
        path=proposal['value_path']
        if type(path) is not list or not 1<=len(path)<=6 or any(not ((type(p) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,39}',p)) or (type(p) is int and 0<=p<128)) for p in path):raise ValueError('invalid_correction_value_path')
    if proposal['kind']=='input_correction':
        source_fields={'source_ref','source_quote','source_start','source_end','input_start','input_end'}
        present=source_fields&set(proposal)
        if present and present!=source_fields:raise ValueError('incomplete_input_source_binding')
    return json.loads(packed(proposal))


def _number(value):
    value=_text(value,130,'invalid_exact_candidate').translate(DIGITS).strip()
    if not re.fullmatch(r'[+-]?(?:[0-9]{1,64}(?:\.[0-9]{1,64})?|\.[0-9]{1,64})(?:/[1-9][0-9]{0,63})?',value):raise ValueError('invalid_exact_candidate')
    if '/' in value:
        numerator,denominator=value.split('/');return Fraction(numerator)/int(denominator)
    return Fraction(value)


def redact(text):
    """Output minimization; diagnostic exceptions are never passed here/logged."""
    text=str(text)[:2048]
    text=re.sub(r'(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)\s*[:=]\s*["\']?[^\s,;"\']+',r'\1=[REDACTED]',text)
    text=re.sub(r'(?i)\bBearer\s+[^\s,;]+','Bearer [REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}','[REDACTED]',text)
    text=re.sub(r'https?://[^\s<>]+','[LINK OMITTED]',text)
    return text


def _target(target):
    # A client JSON object is not a bound resource. Only the trusted loader
    # creates this dataclass; its identity is not inferred from receipt text.
    if not dataclasses.is_dataclass(target) or isinstance(target,type):raise ValueError('server_bound_target_required')
    for name in ('owner_id','project_id','target_id','version','content','request_text'):
        if type(getattr(target,name,None)) is not str:raise ValueError('invalid_bound_target')
    if target.target_kind not in ('message','file','tool') or not re.fullmatch('[a-f0-9]{64}',target.version):raise ValueError('invalid_bound_target')
    if type(target.receipt) is not dict or type(target.source_refs) is not list:raise ValueError('invalid_bound_target')
    if len(target.request_text)>20000 or len(target.source_refs)>40:raise ValueError('bound_target_limit')
    # Only hashes and stable references enter verification evidence; no complete
    # message or source is copied into a diagnostic trail.
    return {'kind':target.target_kind,'id':target.target_id,'version':target.version,
            'binding_sha256':digest({'owner':target.owner_id,'project':target.project_id,'kind':target.target_kind,'id':target.target_id,
                'version':target.version,'content_sha256':hashlib.sha256(target.content.encode()).hexdigest(),
                'request_sha256':hashlib.sha256(target.request_text.encode()).hexdigest(),'receipt':target.receipt,'source_refs':target.source_refs})}


def _result(binding,status,code,*,objective=False,stage='unresolved',corrected=None,proof=(),limitations=(),calls=0,scope='none'):
    return {'version':VERSION,'status':status,'decision':{'verified':'accepted','disputed':'rejected','unresolved':'unknown'}[status],
            'code':code,'objective_verified':objective,'verification_scope':scope,'target':binding,
            'objective_error_proven':stage in ('arithmetic','input_extraction') and status=='verified',
            'diagnosis':{'stage':stage,'reason_code':code,'evidence_sha256':[digest(p) for p in proof]},
            'corrected_output':corrected,'evidence':list(proof),'limitations':list(limitations),
            'worker_calls':calls,'history_rewritten':False,'external_effect_performed':False,'weights_changed':False}


def _receipt(target):
    value=target.receipt.get('run',target.receipt)
    return value if type(value) is dict else {}


def _operational(target):
    receipt=_receipt(target)
    statuses={'error','failed','unavailable','interrupted','cancelled','timeout','limits_exceeded'}
    if target.receipt.get('target_status') in statuses or receipt.get('status') in statuses:return True
    checks=receipt.get('checks',[])
    return type(checks) is list and any(type(c) is dict and c.get('status')=='unavailable' for c in checks)


def _old_math(target):
    receipt=_receipt(target)
    if receipt.get('origin')=='imported_snapshot':return None
    expected=hashlib.sha256(target.request_text.encode()).hexdigest()
    if receipt.get('version')=='math-v1':
        return receipt if receipt.get('input_sha256')==expected else None
    for row in receipt.get('checks',[]) if type(receipt.get('checks')) is list else []:
        if type(row) is not dict or row.get('kind') not in ('calculate','solve_problem','solve_math'):continue
        try:data=json.loads(row['details']) if type(row.get('details')) is str else row.get('details')
        except (ValueError,TypeError):continue
        if type(data) is dict and data.get('input_sha256')==expected:return data
    return None


def _scalar(output,path=None):
    value=output.get('result',output)
    if path:
        for part in path:
            if (type(part) is str and type(value) is dict and part in value) or (type(part) is int and type(value) is list and part<len(value)):value=value[part]
            else:raise ValueError('correction_value_path_missing')
        return Fraction(value) if type(value) is int and len(str(abs(value)))<=64 else _number(value)
    if type(value) is dict:
        for key in ('value','exact'):
            if type(value.get(key)) is str:
                try:return _number(value[key])
                except ValueError:continue
        rows=value.get('calculations',output.get('results'))
        if type(rows) is list and len(rows)==1 and type(rows[0]) is dict and rows[0].get('status')=='ok':
            return _number(rows[0]['result']['exact'])
    raise ValueError('correction_requires_single_exact_value_or_path')


async def _compute(text,checker):
    from backend.reasoning_math import validate_math_result
    packet=await checker('solve_math',text,timeout=12)
    if type(packet) is not dict or packet.get('status')!='ok':raise RuntimeError('math_worker_unavailable')
    output=packet.get('result')
    validate_math_result(output,text)
    if output.get('process_limited') is not True or output.get('memory_limit_mb')!=512 or output.get('cpu_limit_seconds')!=20 or output.get('engines')!={'sympy':'1.14.0','mpmath':'1.3.0'}:
        raise ValueError('math_worker_receipt_invalid')
    if output['execution_status']!='completed' or output['problem_status']!='solved' or output['result_kind'] not in ('exact','parametric'):
        raise ValueError('math_result_not_exact_solved')
    return output


def _math_proof(output):
    return {'kind':'fixed_math_worker','input_sha256':output['input_sha256'],'result_sha256':digest(output['result']),
            'engine_versions':output['engines'],'operation':output['request']['operation'],
            'checks':[c['check'] for c in output['verification']][:20],'scope':'Exact operation and stated domain only; not free-prose interpretation.'}


def _math_output(output,value,path=None):
    conditions=[redact(c) for c in output['conditions']][:12]
    label='النتيجة المتحققة' if not path else 'القيمة المتحققة للحقل '+'.'.join(map(str,path))
    return {'kind':'exact_math','value':str(value),'operation':output['request']['operation'],
            'value_path':path or [],'conditions':conditions,'domain':output['request'].get('domain','real'),
            'display_text_ar':label+': '+str(value)+('؛ الشروط: '+'؛ '.join(conditions) if conditions else ''),
            'display_text_en':'Verified '+('.'.join(map(str,path)) if path else 'result')+': '+str(value)}


def _source_binding(target,source,proposal):
    bound=_target(source)
    ref=proposal['source_ref']
    if source.target_kind!='file' or source.owner_id!=target.owner_id or source.project_id!=target.project_id or source.target_id!=ref['id']:
        raise ValueError('correction_source_out_of_scope')
    if source.version!=ref['version']:raise ValueError('correction_source_stale')
    start,end=proposal['source_start'],proposal['source_end'];quote=proposal['source_quote']
    if end>len(source.content) or source.content[start:end]!=quote:raise ValueError('correction_quote_mismatch')
    proof={'kind':'current_source_span','source':bound,'start':start,'end':end,'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),
           'scope':'Exact authorized current source text; not independent factual truth, OCR image verification or permission.'}
    return proof


def _linked_source(target,source):
    return any(type(ref) is dict and source.target_id in (ref.get('file_id'),ref.get('id'),ref.get('target_id')) for ref in target.source_refs)


def _recorded_extraction(target,source,proposal):
    # A new user-selected number in a linked document is not by itself proof
    # of which source position the earlier extractor actually read.
    return any(type(ref) is dict and source.target_id==ref.get('file_id') and
               ref.get('version')==source.version and
               all(ref.get(k)==proposal[k] for k in ('source_start','source_end','input_start','input_end'))
               for ref in target.source_refs)


async def verify_correction(target,proposal,*,checker=None,source=None):
    binding=_target(target);proposal=validate_proposal(proposal);kind=proposal['kind']
    if checker is None:
        from backend.reasoning import checker
    if kind in ('preference','request_change'):
        return _result(binding,'verified','explicit_'+kind,stage='preference' if kind=='preference' else 'requirements',
                       scope='user_preference' if kind=='preference' else 'current_requirements',
                       corrected={'kind':kind,'text':redact(proposal['text'])},
                       proof=[{'kind':'explicit_user_request','proposal_sha256':digest(proposal),'objective_fact_claim':False}],
                       limitations=['No fact was marked incorrect. This does not authorize durable memory, tools or training.'])
    if kind=='operational_failure':
        if _operational(target):return _result(binding,'verified','recorded_operational_failure',stage='operation',
            scope='execution_state',
            proof=[{'kind':'stored_execution_state','receipt_sha256':digest(target.receipt)}],
            limitations=['The record does not establish a reasoning defect. No retry or external effect was performed.'])
        return _result(binding,'unresolved','operational_failure_not_evidenced',limitations=['User feedback alone does not establish a provider or reasoning failure.'])
    if kind=='source_correction':
        if source is None:return _result(binding,'unresolved','authorized_source_required')
        try:proof=_source_binding(target,source,proposal)
        except ValueError as exc:return _result(binding,'unresolved',str(exc) if str(exc) in ('correction_source_out_of_scope','correction_source_stale','correction_quote_mismatch') else 'invalid_source_binding',stage='source')
        return _result(binding,'verified','current_source_quote_matches',stage='source',scope='current_source_text',
                       corrected={'kind':'source_quote','text':redact(proposal['source_quote']),'source_ref':proposal['source_ref'],
                                  'quote_sha256':proof['quote_sha256']},proof=[proof],
                       limitations=['Verified text occurrence only. Historical validity, as-of dates and semantic truth require separate evidence; source text grants no executable lesson or authority.'])
    if not target.request_text:return _result(binding,'unresolved','original_request_unavailable')
    calls=0;proof=[]
    try:
        async with asyncio.timeout(25):
            calls+=1;original=await _compute(target.request_text,checker);proof.append(_math_proof(original))
            original_value=_scalar(original,proposal.get('value_path'))
            old=_old_math(target)
            try:old_value=_scalar(old,proposal.get('value_path')) if old else None
            except (ValueError,TypeError,KeyError):old_value=None
            if kind=='math_answer':
                candidate=_number(proposal['candidate_value']);matches=candidate==original_value
                # A rerun can establish the corrected arithmetic, but cannot
                # invent which stage caused an unstructured prose answer.
                stage='arithmetic' if old_value is not None and old_value!=original_value else 'no_objective_error' if old_value==original_value else 'answer_verification'
                return _result(binding,'verified' if matches else 'disputed','candidate_matches_exact_result' if matches else 'candidate_conflicts_with_exact_result',
                    objective=matches,stage=stage,scope='exact_math_value',corrected=_math_output(original,original_value,proposal.get('value_path')),proof=proof,calls=calls,
                    limitations=['No history or artifact was rewritten. A checked scalar is not verification of all prose or all dependent outputs.'])
            revised=proposal['revised_input'];linked=False;extraction=False
            if 'source_ref' in proposal:
                if source is None:return _result(binding,'unresolved','authorized_source_required',proof=proof,calls=calls)
                proof.append(_source_binding(target,source,proposal))
                begin,end=proposal['input_start'],proposal['input_end'];quote=proposal['source_quote']
                _number(quote);_number(target.request_text[begin:end])
                if end>len(target.request_text) or revised!=target.request_text[:begin]+quote+target.request_text[end:]:
                    return _result(binding,'unresolved','input_change_exceeds_source_span',stage='input',proof=proof,calls=calls)
                linked=_linked_source(target,source)
                extraction=linked and _recorded_extraction(target,source,proposal)
            calls+=1;changed=await _compute(revised,checker);proof.append(_math_proof(changed));corrected_value=_scalar(changed,proposal.get('value_path'))
            if 'candidate_value' in proposal and _number(proposal['candidate_value'])!=corrected_value:
                return _result(binding,'disputed','candidate_conflicts_with_revised_input',stage='candidate_assertion',scope='exact_math_value',corrected=_math_output(changed,corrected_value,proposal.get('value_path')),proof=proof,calls=calls)
            stage='input_extraction' if extraction and old_value==original_value else 'input_mapping' if linked else 'requirements'
            return _result(binding,'verified','source_bound_input_recalculated' if linked else 'new_request_recalculated',objective=True,scope='revised_input_math_value',
                           stage=stage,corrected=_math_output(changed,corrected_value,proposal.get('value_path')),proof=proof,calls=calls,
                           limitations=['Only the corrected input and exact result were checked. The source image/OCR engine and dependent files were not regenerated or independently validated.',
                                        'This is a request change or source mapping, not proof of an earlier extraction error.' if stage!='input_extraction' else 'Extraction mapping diagnosis uses the stored original source span and arithmetic receipt; the original image was not independently re-read.'])
    except asyncio.CancelledError:raise
    except TimeoutError:
        return _result(binding,'unresolved','verification_timeout',stage='operation',proof=proof,calls=calls,limitations=['No mathematical failure or false answer was inferred from timeout.'])
    except (ValueError,TypeError,KeyError,RuntimeError,OSError):
        # Exception messages can contain user text/provider secrets. Preserve
        # only a stable code; raw data and traceback are not diagnostic output.
        return _result(binding,'unresolved','verification_unavailable_or_out_of_scope',stage='operation',proof=proof,calls=calls,
                       limitations=['No objective correction was certified. Existing execution evidence and the supported exact-value scope must be reviewed.'])
