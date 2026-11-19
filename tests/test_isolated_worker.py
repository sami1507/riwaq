"""Adapter contract simulations: NO Docker or project code is run by these tests.

Actual daemon/container boundary tests must be recorded separately. Fake engine
responses exercise fail-closed policy, cancellation, authority and cleanup only.
"""
import copy
import hashlib
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend import isolated_worker as w
from backend import coding_sandbox as sandbox


@pytest.fixture
def config(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    exe=tmp_path/'docker.exe';exe.write_bytes(b'trusted-placeholder-never-executed')
    return w.IsolatedConfig(docker_executable=str(exe),docker_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        endpoint='npipe:////./pipe/dockerDesktopLinuxEngine',daemon_id='test-daemon',images={'python_unittest':'sha256:'+'a'*64,'node_test':'sha256:'+'b'*64})


def inspected(config,runner='python_unittest'):
    return {'Id':'c'*64,'Image':config.images[runner],'State':{'Status':'created'},'Mounts':[],
        'Config':{'User':'65534:65534','Entrypoint':[w.RUNNERS[runner][0]],'Cmd':w.RUNNERS[runner][1],
                  'OpenStdin':True,'Tty':False,'Healthcheck':{'Test':['NONE']},'Labels':{'riwaq.isolated':w.VERSION}},
        'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'Memory':512*1024**2,
          'MemorySwap':512*1024**2,'PidsLimit':64,'NanoCpus':1_000_000_000,'CapDrop':['ALL'],
          'IpcMode':'private','CgroupnsMode':'private','PidMode':'','UTSMode':'',
          'RestartPolicy':{'Name':'no'},'LogConfig':{'Type':'none'},
          'Tmpfs':{'/tmp':'rw,noexec,nosuid,nodev,size=67108864,mode=1777'},'SecurityOpt':['no-new-privileges=true']}}


class Engine:
    calls=[]; fail_start=None; corrupt=None; remaining=False; authorized=True; starts=0; revoke_after=False
    def __init__(self,config,folder,deadline=None):self.config=config;self.deadline=deadline
    def command(self,args,**kw):
        self.calls.append((args,kw)); raw=b''
        if args[0]=='info':
            value={'ID':self.config.daemon_id,'OSType':'linux','SecurityOptions':['name=seccomp,profile=builtin'],
                'MemoryLimit':True,'SwapLimit':True,'PidsLimit':True,'CpuCfsQuota':True,'ServerVersion':'test'}
        elif args[:2]==['image','inspect']:
            value=[{'Id':args[2],'Os':'linux','Architecture':'amd64','Config':{}}]
        elif args[0]=='create':return w.CLIResult(0,b'c'*64+b'\n',b'')
        elif args[0]=='inspect':
            row=inspected(self.config)
            if self.starts:row['State']={'Status':'exited','Running':False,'ExitCode':0,'OOMKilled':False,'StartedAt':'2026-11-15T10:00:00.000Z'}
            if self.corrupt:self.corrupt(row)
            value=[row]
        elif args[0]=='start':
            type(self).starts+=1
            if self.revoke_after:type(self).authorized=False
            if self.fail_start:raise w.IsolatedError(self.fail_start)
            return w.CLIResult(0,b'claimed 999 passed\n',b'')
        elif args[0]=='rm':return w.CLIResult(0,b'',b'')
        elif args[:2]==['container','ls']:return w.CLIResult(0,b'c'*64 if self.remaining else b'',b'')
        else:raise AssertionError(args)
        return w.CLIResult(0,json.dumps(value).encode(),b'')


@pytest.fixture
def engine(monkeypatch):
    for key,value in dict(calls=[],fail_start=None,corrupt=None,remaining=False,authorized=True,starts=0,revoke_after=False).items():setattr(Engine,key,value)
    monkeypatch.setattr(w,'_Client',Engine);monkeypatch.setattr(w,'_QUARANTINED',False)
    monkeypatch.setattr(w,'_QUARANTINE_NAMES',set())
    def forbidden(*a,**k):raise AssertionError('Unexpected host execution')
    monkeypatch.setattr(w.subprocess,'Popen',forbidden)
    return Engine


def run(config,**kw):
    return w.execute({'test_one.py':b'UNTRUSTED DATA ONLY'},'python_unittest',['test_one.py'],
        config=config,authorize=kw.pop('authorize',lambda:Engine.authorized),**kw)


def test_success_has_supervisor_exit_but_never_fake_test_success(config,engine):
    events=[];result=run(config,progress=events.append)
    assert result['status']=='completed' and result['exit_code']==0 and result['runner_exit_zero'] is True
    assert result['tests_passed'] is None and result['test_count'] is None and not result['test_count_verified']
    assert result['policy_inspected'] and result['cleanup_verified'] and not result['isolation_verified']
    assert events[0]['stage']=='container_created'
    assert not result['host_fallback'] and not result['automatic_download']
    start=next(kw for args,kw in engine.calls if args[0]=='start')
    assert json.loads(start['payload'])['targets']==['test_one.py']


@pytest.mark.parametrize('reason',['cancelled','timeout','output_limit','docker_client_unavailable'])
def test_execution_failures_always_remove_owned_container(config,engine,reason):
    engine.fail_start=reason;result=run(config)
    assert result['reason_code']==reason and result['cleanup_verified']
    assert any(args[0]=='rm' for args,_ in engine.calls)
    assert result['tests_passed'] is None


def test_source_revoked_after_execution_discards_output(config,engine):
    engine.revoke_after=True;result=run(config)
    assert result['status']=='revoked' and result['stdout']=='' and result['runner_exit_zero'] is None
    assert result['cleanup_verified']


def test_revoked_before_dispatch_no_engine_calls(config,engine):
    result=run(config,authorize=lambda:False)
    assert result['status']=='revoked' and not engine.calls and not result['executed']


def test_cancelled_before_dispatch_no_engine_calls(config,engine):
    stop=threading.Event();stop.set();result=run(config,stop=stop)
    assert result['status']=='cancelled' and not engine.calls


def test_cleanup_failure_quarantines_subsequent_execution(config,engine):
    engine.remaining=True;result=run(config)
    assert result['status']=='cleanup_failed' and w._QUARANTINED and result['stdout']==''
    engine.calls.clear();second=run(config)
    assert second['status']=='cleanup_failed' and not engine.calls


@pytest.mark.parametrize('field,value',[
    ('NetworkMode','host'),('ReadonlyRootfs',False),('Privileged',True),('Memory',0),
    ('MemorySwap',-1),('NanoCpus',0),('PidsLimit',0),('CapAdd',['SYS_ADMIN']),
    ('Binds',['/:/host']),('Devices',[{'PathOnHost':'/dev/kvm'}]),('SecurityOpt',['seccomp=unconfined']),
    ('CgroupnsMode','host'),('PidMode','host'),('IpcMode','host'),('PortBindings',{'80/tcp':[{}]}),
])
def test_daemon_policy_mismatch_never_starts(config,engine,field,value):
    engine.corrupt=staticmethod(lambda row:row['HostConfig'].__setitem__(field,value))
    result=run(config)
    assert result['reason_code']=='container_policy_mismatch' and engine.starts==0
    assert result['cleanup_verified']


def test_create_options_no_mount_pull_shell_or_privilege(config):
    args=w.create_arguments(config,'python_unittest','riwaq-isolated-'+'0'*32,512,30)
    for bad in ('--mount','--volume','--privileged','--env-file','--gpus','--publish','--pid','--use-api-socket'):assert bad not in args
    assert args[args.index('--pull')+1]=='never' and args[args.index('--network')+1]=='none'
    assert args[args.index('--entrypoint')+1]=='/usr/local/bin/python'
    assert '--read-only' in args and '--interactive' in args


@pytest.mark.parametrize('path',['../x.py','a/../x.py','C:/x.py','/x.py','a\\x.py','.git/x.py','.env','secrets.py','x.py;whoami.py'])
def test_snapshot_paths_refused_without_process(path,config,engine):
    result=w.execute({path:b'x'},'python_unittest',[path],config=config,authorize=lambda:True)
    assert result['status']=='blocked' and not engine.calls


def test_file_limits_and_missing_targets(config,engine):
    with pytest.raises(w.IsolatedError):w.prepare_payload({'t.py':b'a'*(w.MAX_FILE+1)},'python_unittest',['t.py'])
    with pytest.raises(w.IsolatedError):w.prepare_payload({'t.py':b''},'python_unittest',['missing.py'])
    with pytest.raises(w.IsolatedError):w.prepare_payload({'a':b'','a/t.py':b''},'python_unittest',['a/t.py'])
    with pytest.raises(w.IsolatedError):w.prepare_payload({'T.py':b'','t.py':b''},'python_unittest',['t.py'])
    assert not engine.calls


@pytest.mark.parametrize('runner,target',[('python_unittest','-test.py'),('node_test','--test-reporter=evil.js')])
def test_target_cannot_be_interpreted_as_runner_flag(runner,target,config,engine):
    result=w.execute({target:b'inert'},runner,[target],config=config,authorize=lambda:True)
    assert result['reason_code']=='target_not_in_snapshot' and not engine.calls


def test_start_attempt_without_observed_started_state_never_claims_execution(config,engine):
    def no_start(row):
        if engine.starts:row['State']={'Status':'exited','Running':False,'ExitCode':0,'StartedAt':'0001-01-01T00:00:00Z'}
    engine.corrupt=staticmethod(no_start)
    value=run(config)
    assert value['start_attempted'] and not value['executed'] and value['reason_code']=='container_start_unverified'
    assert value['cleanup_verified'] and value['runner_exit_zero'] is None


@pytest.mark.parametrize('change',[{'images':{'python_unittest':'python:3.12-slim'}},{'endpoint':'tcp://127.0.0.1:2375'},
    {'endpoint':'ssh://host'},{'network':'host'},{'docker_sha256':'nohash'}])
def test_config_rejects_tags_remote_daemons_and_overrides(config,change):
    with pytest.raises(ValidationError):w.IsolatedConfig.model_validate({**config.model_dump(),**change})


def test_docker_executable_hash_verified_before_spawn(config,tmp_path):
    assert w._verified_executable(config)==str(Path(config.docker_executable).resolve())
    Path(config.docker_executable).write_bytes(b'changed')
    with pytest.raises(w.IsolatedError,match='changed'):w._verified_executable(config)


def test_client_environment_never_inherits_secrets_or_remote_docker(config,tmp_path,monkeypatch):
    monkeypatch.setenv('DOCKER_HOST','tcp://evil:2375');monkeypatch.setenv('OPENAI_API_KEY','synthetic-secret')
    monkeypatch.setenv('DOCKER_CONFIG','C:/private');monkeypatch.setenv('HTTP_PROXY','http://proxy')
    client=w._Client(config,tmp_path)
    assert not {'DOCKER_HOST','DOCKER_CONFIG','OPENAI_API_KEY','HTTP_PROXY'} & client.env.keys()
    assert client.base[-2:]==['--host',config.endpoint]


def test_server_owned_config_only_and_legacy_path_always_blocked(config,tmp_path,engine,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    value=sandbox.run_snapshot({'test_one.py':b'x'},{'runner':'python_unittest','targets':['test_one.py']},authorize=lambda:True)
    assert not value['executed'] and not engine.calls
    (tmp_path/'isolated-workers.json').write_text(config.model_dump_json(),encoding='utf-8')
    status=sandbox.status();assert status['configured'] and not status['available'] and not engine.calls
    legacy=sandbox.run_tests(tmp_path,{'runner':'python_unittest','targets':['test_one.py']})
    assert not legacy['executed'] and not engine.calls
    result=sandbox.run_snapshot({'test_one.py':b'x'},{'runner':'python_unittest','targets':['test_one.py']},authorize=lambda:True)
    assert result['status']=='completed'


def test_raw_project_path_cannot_enter_snapshot_adapter(config,engine,tmp_path):
    value=w.execute(tmp_path,'python_unittest',['test.py'],config=config,authorize=lambda:True)
    assert value['reason_code']=='snapshot_file_limit' and not engine.calls


def test_recovery_rejects_nonowned_names(config,engine):
    with pytest.raises(w.IsolatedError,match='container_name_invalid'):w.cleanup_owned(config,'other-project')
    assert not engine.calls


def test_cancel_during_authorization_prevents_client_spawn(config,tmp_path,monkeypatch):
    stop=threading.Event();calls=[]
    client=w._Client(config,tmp_path)
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**k:calls.append('spawn'))
    def authorize():
        stop.set()
        return True
    with pytest.raises(w.IsolatedError,match='^cancelled$'):
        client.command(['start','--attach','synthetic'],stop=stop,authorize=authorize)
    assert not calls


def test_deadline_during_authorization_prevents_client_spawn(config,tmp_path,monkeypatch):
    clock=[100.0];calls=[]
    client=w._Client(config,tmp_path)
    monkeypatch.setattr(w.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**k:calls.append('spawn'))
    def authorize():
        clock[0]=111.0
        return True
    with pytest.raises(w.IsolatedError,match='^timeout$'):
        client.command(['start','--attach','synthetic'],seconds=10,authorize=authorize)
    assert not calls
