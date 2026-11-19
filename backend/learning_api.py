"""Authenticated local feedback workflow. No training or external write endpoint."""
from __future__ import annotations
import sqlite3
import re
from datetime import datetime,timezone,timedelta
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from backend import guard, local_auth, learning
from backend.db import get_db

router=APIRouter(prefix='/api/projects/{pid}/learning',tags=['learning'])

class Strict(BaseModel):
    model_config={'extra':'forbid'}

class Target(Strict):
    kind:Literal['message','file','tool']
    id:str=Field(min_length=1,max_length=100)
    version:str=Field(pattern=r'^[0-9a-f]{64}$')

class FeedbackInput(Strict):
    idempotency_key:str=Field(min_length=8,max_length=100)
    target:Target
    alternative_target:Target|None=None
    signal:Literal['negative','positive','regenerate','abandon','correction','preference','request_change','operational_failure','comparison','example','remember']
    dimensions:list[Literal['correctness','style','completeness','input','sources','tools','latency','unspecified']]=Field(default_factory=lambda:['unspecified'],max_length=8)
    reason:str=Field(default='',max_length=2000)
    correction:dict|None=None
    scope:Literal['conversation','project','personal']='conversation'
    purposes:list[Literal['current_task','project_memory','product_improvement','training']]=Field(default_factory=lambda:['current_task'],max_length=4)
    conversation_id:str|None=Field(default=None,max_length=100)

class Version(Strict):
    expected_version:int=Field(ge=1,strict=True)

class CandidateInput(Version):
    rule:dict
    applicability:dict
    scope:Literal['conversation','project','personal']='conversation'
    conversation_id:str|None=Field(default=None,max_length=100)

class ActivateInput(Version):
    evaluation_id:str=Field(min_length=1,max_length=100)
    expected_active_id:str|None=Field(default=None,max_length=100)

class CandidateEdit(Version):
    rule:dict
    applicability:dict

class ConsentWithdrawal(Version):
    purposes:list[Literal['current_task','project_memory','product_improvement','training']]=Field(min_length=1,max_length=4)

class DraftExport(Version):
    rights_confirmed:Literal[True]

def actor():
    who=local_auth.current_principal()
    return guard.Actor(who['owner_id'],who['session_id'])

def invoke(function,*args,**kwargs):
    try:
        with get_db() as c:return function(c,actor(),*args,**kwargs)
    except learning.LearningError as exc:
        raise HTTPException(exc.status_code,exc.code) from exc
    except guard.Denied as exc:
        raise HTTPException(404,'Learning resource is not available') from exc
    except ValueError as exc:
        raise HTTPException(422,'Invalid learning input') from exc
    except sqlite3.Error as exc:
        raise HTTPException(503,'Learning storage unavailable; no success confirmed') from exc

@router.get('/targets/{kind}/{target_id}')
def target(pid:str,kind:Literal['message','file','tool'],target_id:str):
    return invoke(learning.target_snapshot,pid,kind,target_id)

@router.get('/feedback')
def feedback_list(pid:str):return invoke(learning.list_feedback,pid)

@router.post('/feedback')
def feedback_create(pid:str,body:FeedbackInput):
    payload=body.model_dump(exclude_none=True,exclude={'idempotency_key'})
    return invoke(learning.add_feedback,pid,payload,body.idempotency_key)

@router.get('/feedback/{fid}')
def feedback_get(pid:str,fid:str):return invoke(learning.get_feedback,pid,fid)

@router.delete('/feedback/{fid}')
def feedback_delete(pid:str,fid:str,expected_version:int):
    return invoke(learning.delete_feedback,pid,fid,expected_version=expected_version)

@router.post('/feedback/{fid}/withdraw-consent')
def feedback_withdraw(pid:str,fid:str,body:ConsentWithdrawal):
    return invoke(learning.withdraw_consent,pid,fid,**body.model_dump())

@router.post('/feedback/{fid}/training-draft')
def training_draft(pid:str,fid:str,body:DraftExport):
    """A consented exact-arithmetic SFT draft; no training job or dataset approval.

    Export is deliberately restricted to plain arithmetic inputs and exact
    verified values. A quote, preference or arbitrary prose isn't a gold label.
    """
    from training.learning_data import DataApproval,record_sha256,validate_examples
    def prepare(c,who):
        feedback=learning.get_feedback(c,who,pid,fid)
        if feedback['version']!=body.expected_version:raise learning.LearningError(409,'feedback_version_conflict')
        if 'training' not in feedback.get('purposes',[]):raise learning.LearningError(403,'training_consent_required')
        ref=feedback['target'];bound=learning.bind_target(c,who,pid,ref['kind'],ref['id'],ref['version'])
        proof=feedback.get('verification',{});result=proof.get('corrected_output',{})
        proposal=feedback.get('correction',{})
        if (feedback.get('status')!='verified' or not proof.get('objective_verified') or
            proof.get('verification_scope')!='exact_math_value' or proposal.get('kind')!='math_answer' or
            result.get('kind')!='exact_math' or proposal.get('value_path') or
            not re.fullmatch(r'[\d٠-٩۰-۹\s+*/().^−\-٫]+',bound.request_text) or len(bound.request_text)>1000):
            raise learning.LearningError(422,'draft_requires_verified_plain_arithmetic')
        row={'id':'feedback-'+fid+'-v'+str(feedback['version']),'kind':'sft',
             'messages':[{'role':'user','content':bound.request_text},{'role':'assistant','content':result['value']}]}
        stamp=datetime.now(timezone.utc)
        row_hash=record_sha256(row)
        approval=DataApproval(record_sha256=row_hash,owner_id=who.owner_id,project_id=pid,
            purpose='sft',family_id='exact-arithmetic',source_ids=('feedback:'+fid,'message:'+ref['id']),
            rights_reference='owner-declaration:'+fid+':'+str(feedback['version']),
            consent_reference=feedback['consent_policy']+':'+fid+':'+str(feedback['version']),
            review_reference='exact-math-verifier:'+learning.digest(proof),reviewed_at=stamp,expires_at=stamp+timedelta(days=30),
            allowed_uses=('training',),target_verdict='correct')
        report=validate_examples([row],approvals={row['id']:approval},owner_id=who.owner_id,project_id=pid,revoked_sources=frozenset(),now=stamp)
        if report.get('errors'):raise learning.LearningError(422,'training_draft_preflight_failed')
        learning.register_descendant(c,who,pid,fid,'dataset','draft:'+row_hash)
        return {'status':'draft_example','record':row,'validation':report,'record_sha256':row_hash,
            'source_feedback':{'id':fid,'version':feedback['version']},'training_ready':False,'training_started':False,
            'model_changed':False,'rights_basis':'Explicit owner declaration, not independent copyright review',
            'limitations':['One reviewed example is not a split dataset or training authorization.',
                'Withdrawing consent prevents server re-export; a downloaded copy cannot be remotely erased.',
                'No model weights were trained or erased.']}
    return invoke(prepare)

@router.post('/feedback/{fid}/verify')
async def feedback_verify(pid:str,fid:str,body:Version):
    from backend.learning_verification import verify_correction
    feedback=invoke(learning.get_feedback,pid,fid)
    if feedback['version']!=body.expected_version:raise HTTPException(409,'feedback_version_conflict')
    if not feedback.get('correction'):raise HTTPException(422,'No correction supplied; feedback alone does not establish truth')
    ref=feedback['target']
    bound=invoke(learning.bind_target,pid,ref['kind'],ref['id'],ref['version'])
    proposal=feedback['correction']
    source_ref=proposal.get('source_ref')
    source=invoke(learning.bind_target,pid,source_ref['kind'],source_ref['id'],source_ref['version']) if source_ref else None
    # The fixed verifier has its own bounded workers. No DB lock is held while
    # running them; record_verification rechecks source versions and the CAS.
    proof=await verify_correction(bound,proposal,source=source)
    return invoke(learning.record_verification,pid,fid,body.expected_version,proof)

@router.post('/feedback/{fid}/candidates')
def candidate_create(pid:str,fid:str,body:CandidateInput):
    return invoke(learning.propose_candidate,pid,fid,**body.model_dump())

@router.get('/candidates')
def candidate_list(pid:str):return invoke(learning.list_candidates,pid)

@router.get('/candidates/{cid}')
def candidate_get(pid:str,cid:str):return invoke(learning.get_candidate,pid,cid)

@router.put('/candidates/{cid}')
def candidate_edit(pid:str,cid:str,body:CandidateEdit):
    return invoke(learning.update_candidate,pid,cid,**body.model_dump())

@router.post('/candidates/{cid}/revoke')
def candidate_revoke(pid:str,cid:str,body:Version):
    return invoke(learning.revoke_candidate,pid,cid,body.expected_version)

@router.post('/candidates/{cid}/evaluate')
def candidate_evaluate(pid:str,cid:str,body:Version):
    from backend.learning_evaluation import evaluate_candidate
    candidate=invoke(learning.get_candidate,pid,cid)
    if candidate['version']!=body.expected_version:raise HTTPException(409,'candidate_version_conflict')
    baseline=invoke(learning.active_rules,pid,candidate['conversation_id'],'')['rules']
    proof=evaluate_candidate(candidate,baseline)
    evaluation=invoke(learning.record_evaluation,pid,cid,body.expected_version,proof)
    return {'candidate':invoke(learning.get_candidate,pid,cid),'evaluation':evaluation}

@router.post('/candidates/{cid}/activate')
def candidate_activate(pid:str,cid:str,body:ActivateInput):
    return invoke(learning.activate_candidate,pid,cid,**body.model_dump())

@router.get('/releases')
def release_list(pid:str):return invoke(learning.list_releases,pid)

@router.get('/releases/{rid}')
def release_get(pid:str,rid:str):return invoke(learning.get_release,pid,rid)

@router.get('/context')
def context_preview(pid:str,conversation_id:str,question:str=''):
    if len(question)>8000:raise HTTPException(422,'Question exceeds context preview limit')
    return invoke(learning.active_rules,pid,conversation_id,question)

@router.get('/metrics')
def metrics(pid:str):
    from collections import Counter
    rows=invoke(learning.list_feedback,pid)
    candidates=invoke(learning.list_candidates,pid)
    releases=invoke(learning.list_releases,pid)
    return {'scope':'current authenticated owner and project','period':'most recent 200 stored records per entity',
        'feedback_denominator':len(rows),'feedback_statuses':dict(Counter(r['status'] for r in rows)),
        'signals':dict(Counter(r['signal'] for r in rows)),
        'objective_errors_proven':sum(r.get('verification',{}).get('objective_error_proven') is True for r in rows),
        'incorrect_corrections_rejected':sum(r.get('verification',{}).get('status')=='disputed' for r in rows),
        'candidate_denominator':len(candidates),'candidate_statuses':dict(Counter(r['status'] for r in candidates)),
        'release_denominator':len(releases),'release_statuses':dict(Counter(r['status'] for r in releases)),
        'unmeasured':['task success rate','error recurrence per comparable exposure','general model improvement','calibrated confidence'],
        'notes':['Feedback counts do not establish independent evidence, correctness or learning.',
                 'Local single-owner authorization is not hosted multi-tenant verification.']}

@router.post('/releases/{rid}/rollback')
def release_rollback(pid:str,rid:str,body:Version):
    return invoke(learning.rollback_release,pid,rid,expected_version=body.expected_version)

@router.get('/status')
def status(pid:str):
    with get_db() as c:
        try:guard.check_actor(c,actor(),pid)
        except guard.Denied as exc:raise HTTPException(404,'Learning resource is not available') from exc
    return {'feedback':'available','correction':'bounded_local_verification',
            'persistent_personal_memory':{'available':False,'reason':'Existing stage8 security acceptance gate'},
            'shared_global_changes':{'available':False,'reason':'No arbitrary self-modification or shared rollout through feedback'},
            'training':{'started':False,'model_changed':False,'feedback_auto_training':False,
                        'historical_experiment':'Local QLoRA experiment documented in training/README.md; rejected quality gate, not deployed',
                        'current_requirement':'Validated consented dataset, demonstrated need, frozen evaluation and separately authorized training run'},
            'consent_policy':'learning-consent-v1','default_purposes':['current_task']}
