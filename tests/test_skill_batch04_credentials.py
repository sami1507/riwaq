"""Credential CLI routing with a fake store only; never touches real secrets."""
import getpass,runpy,sys,types
from pathlib import Path
import pytest


@pytest.mark.parametrize('argv,expected',[
    (['mail','--account-id','a'*32],('set','mail:'+'a'*32)),
    (['mail','--account-id','b'*32,'--delete'],('delete','mail:'+'b'*32)),
    (['openai'],('set','openai')),
])
def test_scoped_credential_reference_preserves_existing_providers(monkeypatch,capsys,argv,expected):
    calls=[]
    fake=types.SimpleNamespace(get_keyring=lambda:types.SimpleNamespace(priority=1),
      set_password=lambda service,key,value:calls.append(('set',service,key,value)),
      delete_password=lambda service,key:calls.append(('delete',service,key)))
    monkeypatch.setitem(sys.modules,'keyring',fake)
    monkeypatch.setattr(getpass,'getpass',lambda _: 'SYNTHETIC-CLI-CREDENTIAL')
    monkeypatch.setattr(sys,'argv',['set-key.py',*argv])
    runpy.run_path(str(Path('scripts/set-key.py')),run_name='__main__')
    assert len(calls)==1 and (calls[0][0],calls[0][2])==expected and calls[0][1]=='Riwaq'
    assert 'SYNTHETIC-CLI-CREDENTIAL' not in capsys.readouterr().out


@pytest.mark.parametrize('argv',[['mail'],['mail','--account-id','../../other'],['openai','--account-id','a'*32]])
def test_invalid_account_rejected_before_any_store_access(monkeypatch,argv):
    def forbidden():raise AssertionError('A bad reference must never access the credential store')
    monkeypatch.setitem(sys.modules,'keyring',types.SimpleNamespace(get_keyring=forbidden))
    monkeypatch.setattr(sys,'argv',['set-key.py',*argv])
    with pytest.raises(SystemExit) as error:runpy.run_path('scripts/set-key.py',run_name='__main__')
    assert error.value.code==2
