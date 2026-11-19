"""Read-only/ephemeral project-scoped medical education routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from backend.db import get_db
from backend.guard import require_project
from backend.medical_schema import Explain
from backend import medical_education

router=APIRouter(prefix='/api/projects/{project_id}/medical')

def deliver(project_id,operation):
    with get_db() as conn:actor=require_project(conn,project_id)
    result=operation()
    with get_db() as conn:require_project(conn,project_id,actor)
    return JSONResponse(result,headers={'Cache-Control':'no-store','Pragma':'no-cache'})

@router.get('/capabilities')
def capabilities(project_id:str):return deliver(project_id,medical_education.capabilities)

@router.post('/explain')
def explain(project_id:str,body:Explain):return deliver(project_id,lambda:medical_education.explain(body))
