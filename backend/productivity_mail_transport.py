"""Optional fixed SMTP submission adapter; credentials stay in OS keyring.

Operator configuration is separate from user/model mail inputs. DNS is checked
and the actual TCP connection is pinned; TLS keeps the original server name.
"""
from __future__ import annotations
import json
import re
import smtplib
import socket
import ssl
import time
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator
from backend.db import data_dir
from backend import providers,knowledge_fetch

class TransportError(ValueError):pass

class SMTPConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool=False
    host:str=Field(min_length=3,max_length=253)
    port:Literal[465,587]=465
    tls:Literal['implicit','starttls']='implicit'
    username:str=Field(min_length=1,max_length=254)
    fromAddress:str=Field(min_length=3,max_length=254)
    allowedRecipients:list[str]=Field(default_factory=list,max_length=100)
    @field_validator('host')
    @classmethod
    def host_ok(cls,v):
        if not v.isascii() or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',v) or '..' in v:raise ValueError('Invalid SMTP hostname')
        return v.lower()
    @field_validator('username','fromAddress')
    @classmethod
    def no_controls(cls,v):
        if any(ord(c)<32 or ord(c)==127 for c in v):raise ValueError('Invalid account header')
        return v

def configuration(account_id):
    path=data_dir()/'mail-accounts.json'
    if not path.is_file():raise TransportError('smtp_not_configured')
    try:
        if path.stat().st_size>65536:raise ValueError()
        value=json.loads(path.read_text(encoding='utf-8'))
        if set(value)!={'version','accounts'} or value['version']!=1 or not isinstance(value['accounts'],dict) or len(value['accounts'])>20:raise ValueError()
        config=SMTPConfig.model_validate(value['accounts'][account_id])
        if not config.enabled or (config.port,config.tls) not in {(465,'implicit'),(587,'starttls')}:raise ValueError()
        from backend.productivity_mail import address
        if len(set(config.allowedRecipients))!=len(config.allowedRecipients):raise ValueError()
        config.fromAddress=address(config.fromAddress)
        config.allowedRecipients=[address(x) for x in config.allowedRecipients]
        return config
    except (ValueError,KeyError,TypeError,OSError):raise TransportError('smtp_configuration_unavailable') from None

class PinnedSMTP(smtplib.SMTP):
    def __init__(self,host,ip,port):
        self._pinned_ip=ip
        super().__init__(host,port,local_hostname='riwaq.local',timeout=15)
    def _get_socket(self,host,port,timeout):return socket.create_connection((self._pinned_ip,port),timeout)

class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self,host,ip,port):
        self._pinned_ip=ip
        super().__init__(host,port,local_hostname='riwaq.local',timeout=15,context=ssl.create_default_context())
    def _get_socket(self,host,port,timeout):
        sock=socket.create_connection((self._pinned_ip,port),timeout)
        try:return self.context.wrap_socket(sock,server_hostname=host)
        except Exception:sock.close();raise

def _connect(config,ip):
    connection=(PinnedSMTPSSL if config.tls=='implicit' else PinnedSMTP)(config.host,ip,config.port)
    connection.ehlo_or_helo_if_needed()
    if config.tls=='starttls':
        connection.starttls(context=ssl.create_default_context());connection.ehlo()
    return connection

def submit(config,account_id,sender,recipients,wire,authorize,stop):
    """One bounded SMTP transaction. No retries or alternate destinations."""
    connection=None;data_started=False;deadline=time.monotonic()+60
    def gate():
        if stop.is_set() or time.monotonic()>=deadline:raise TransportError('mail_cancelled_or_timeout')
        authorize()
        if connection is not None and connection.sock is not None:connection.sock.settimeout(max(.05,min(15,deadline-time.monotonic())))
    try:
        gate()
        if stop.is_set():raise TransportError('mail_cancelled')
        ips=knowledge_fetch.resolve_public(config.host,timeout=5)
        gate()
        secret=providers.secret('mail:'+account_id)
        if not secret:raise TransportError('smtp_credential_missing')
        connection=_connect(config,ips[0]);gate()
        connection.login(config.username,secret);secret=None
        gate()
        code,_=connection.mail(sender)
        if code!=250:return {'state':'rejected','stage':'sender','smtp_code':int(code),'accepted_by_smtp':False,'delivered':False,'automatic_retry':False}
        refused=[]
        for recipient in recipients:
            gate()
            if stop.is_set():raise TransportError('mail_cancelled')
            code,_=connection.rcpt(recipient)
            if code not in {250,251}:refused.append({'recipient':recipient,'smtp_code':int(code)})
        if refused:
            connection.rset()
            return {'state':'rejected','stage':'recipients','refused':refused,'accepted_by_smtp':False,'delivered':False,'automatic_retry':False}
        gate()
        if stop.is_set():raise TransportError('mail_cancelled')
        data_started=True
        code,_=connection.data(wire)
        return {'state':'smtp_accepted' if code==250 else 'rejected','stage':'data','smtp_code':int(code),'accepted_by_smtp':code==250,'delivered':False,'automatic_retry':False}
    except smtplib.SMTPDataError as exc:
        return {'state':'rejected','stage':'data','smtp_code':int(exc.smtp_code),'accepted_by_smtp':False,'delivered':False,'automatic_retry':False}
    except (smtplib.SMTPException,OSError,ValueError,knowledge_fetch.FetchError):
        return {'state':'unknown' if data_started else 'failed','stage':'data' if data_started else 'connection_or_authorization',
            'error':'smtp_outcome_unknown' if data_started else 'smtp_connection_or_authorization_failed',
            'accepted_by_smtp':None if data_started else False,'delivered':False,'automatic_retry':False}
    finally:
        if connection is not None:
            try:connection.close()
            except OSError:pass
