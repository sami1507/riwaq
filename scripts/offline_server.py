"""Test-only server with an instrumented asyncio/socket network guard.

This is process-level Python API instrumentation, not an OS sandbox. The
separate Ollama process uses installed weights and is outside this guard.
"""
import asyncio
import os
from evaluate_knowledge import deny_external_connections

async def main():
    import uvicorn
    from fastapi.routing import APIRoute
    from backend.app import app
    with deny_external_connections() as proof:
        try:
            await asyncio.get_running_loop().create_connection(asyncio.Protocol,'203.0.113.1',443)
        except OSError:
            assert proof['external_blocked']==['203.0.113.1'], 'The guard itself must reject the external probe'
        else:
            raise RuntimeError('Offline test guard allowed an external connection')
        async def status():return proof
        app.router.routes.insert(0,APIRoute('/api/offline-proof',status,methods=['GET']))
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(os.environ.get('RIWAQ_TEST_PORT','18765')),access_log=False,log_level='warning'))
        await server.serve()

if __name__=='__main__':asyncio.run(main())
