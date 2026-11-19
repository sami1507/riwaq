"""Shared local-owner request ceiling. Not a replacement for worker budgets."""
import sqlite3
import time
from fastapi.responses import JSONResponse
from backend.db import get_db

REQUESTS_PER_MINUTE=600


def claim(owner):
    window=int(time.time()//60)
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT window,count FROM guard_rate_state WHERE owner_id=?',(owner,)).fetchone()
        count=row['count'] if row and row['window']==window else 0
        if count>=REQUESTS_PER_MINUTE:return False
        c.execute('INSERT OR REPLACE INTO guard_rate_state VALUES(?,?,?)',(owner,window,count+1))
    return True


class GuardRateMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        principal=scope.get('riwaq_principal')
        protected=scope['type']=='http' and scope['path'].startswith('/api/') and principal
        # Leave cancellation/revocation usable when an account hits its ceiling.
        control=scope['method']=='POST' and (scope['path'].endswith(('/stop','/cancel')) or scope['path']=='/api/auth/logout') if protected else False
        if protected and not control:
            try:allowed=claim(principal['owner_id'])
            except sqlite3.Error:
                return await JSONResponse({'detail':'Request budget store unavailable'},503)(scope,receive,send)
            if not allowed:
                return await JSONResponse({'detail':'Local owner request ceiling reached; stop or sign out remains available'},429,headers={'Retry-After':str(60-int(time.time()%60))})(scope,receive,send)
        return await self.app(scope,receive,send)
