"""Test-only source server: deny outbound connections, including Ollama."""
import asyncio
import ipaddress
import socket
import os
import uvicorn
from fastapi.routing import APIRoute
from backend.app import app

async def main():
    loop=asyncio.get_running_loop()
    proof={'instance':os.environ['RIWAQ_CALCULATOR_TEST_RUN'],'guard':'Python socket and active asyncio outbound APIs; not an OS firewall','blocked_attempts':[],'self_test':[]}
    original_getaddrinfo=socket.getaddrinfo
    def deny(address):
        host=str(address[0] if isinstance(address,tuple) else address)
        proof['blocked_attempts'].append(host)
        raise OSError('Outbound connection blocked by calculator test guard')
    def connect(sock,address):return deny(address)
    def create(address,*args,**kwargs):return deny(address)
    async def async_connect(factory,host=None,port=None,**kwargs):return deny((host,port))
    async def sock_connect(sock,address):return deny(address)
    def resolve(host,*args,**kwargs):
        try:ipaddress.ip_address(host)
        except ValueError:return deny(host)
        return original_getaddrinfo(host,*args,**kwargs)
    socket.socket.connect=connect
    socket.socket.connect_ex=connect
    socket.create_connection=create
    socket.getaddrinfo=resolve
    loop.create_connection=async_connect
    loop.sock_connect=sock_connect
    for host,port in [('127.0.0.1',11434),('203.0.113.1',443)]:
        try:await loop.create_connection(asyncio.Protocol,host,port)
        except OSError:proof['self_test'].append({'host':host,'blocked':True})
        else:raise RuntimeError('Guard self-test failed')
    proof['blocked_attempts'].clear()
    async def status():return proof
    app.router.routes.insert(0,APIRoute('/api/calculator-offline-proof',status,methods=['GET']))
    await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(os.environ.get('RIWAQ_TEST_PORT','18766')),access_log=False,log_level='warning')).serve()

if __name__=='__main__':asyncio.run(main())
