"""Current-authority downloads and version metadata for skill artifacts."""
from urllib.parse import quote
from fastapi import APIRouter
from fastapi.responses import Response
from backend.db import get_db
from backend import skill_files,skills

router=APIRouter()

@router.get('/api/projects/{pid}/skill-files')
def files(pid:str):
    with get_db() as c:
        who=skills.actor();skills.authorize(c,who,pid)
        return skill_files.list_files(c,who,pid)

@router.get('/api/projects/{pid}/skill-files/{aid}/versions')
def history(pid:str,aid:str):
    with get_db() as c:
        who=skills.actor();skills.authorize(c,who,pid)
        return skill_files.history(c,who,pid,aid)

def content(pid,aid,version=None):
    with get_db() as c:
        who=skills.actor();skills.authorize(c,who,pid)
        h,v=skill_files.version(c,who,pid,aid,version)
        return Response(v['content'],media_type=v['mime'],headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(h['name']),
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'X-Riwaq-File-Version':str(v['version']),'ETag':'"'+v['sha256']+'"'})

@router.get('/api/projects/{pid}/skill-files/{aid}/download')
def latest(pid:str,aid:str):return content(pid,aid)

@router.get('/api/projects/{pid}/skill-files/{aid}/versions/{number}/download')
def fixed(pid:str,aid:str,number:int):return content(pid,aid,number)
