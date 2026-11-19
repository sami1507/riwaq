"""Authenticated read-only runtime diagnostics and explicit synthetic probe."""
from fastapi import APIRouter,HTTPException
from backend.db import get_db,settings
from backend import skills,providers
from backend.skill_observability import snapshot,audit_trace
router=APIRouter()

@router.get('/api/projects/{pid}/skill-runtime')
async def runtime(pid:str):
    who=skills.actor()
    with get_db() as c:
        skills.authorize(c,who,pid);observed=snapshot(c,who,pid)
    cfg=settings();model=await providers.runtime_status(cfg['model'],cfg,probe=False)
    with get_db() as c:skills.authorize(c,who,pid)
    from backend.skill_release import stored_decision
    from backend.skill_core import ROOT
    from backend.db import data_dir
    return {'project_id':pid,'monitoring':observed,'model':model,'release':stored_decision(ROOT,data_dir())}

@router.get('/api/projects/{pid}/skill-runs/{rid}/audit')
def audit(pid:str,rid:str):
    who=skills.actor()
    with get_db() as c:
        row=skills.checked_row(c,pid,rid,who);skills.recheck(c,row,who)
        return {'project_id':pid,'run_id':rid,**audit_trace(c,who,pid,rid)}

@router.post('/api/projects/{pid}/skill-runtime/probe')
async def probe(pid:str):
    who=skills.actor()
    with get_db() as c:skills.authorize(c,who,pid)
    cfg=settings();result=await providers.runtime_status(cfg['model'],cfg|{'_runtime_scope':who.owner_id},probe=True)
    with get_db() as c:skills.authorize(c,who,pid)
    return {'project_id':pid,'model':result,'synthetic_only':True,'external_calls':False}
