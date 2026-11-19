"""Status reuses the execution contract; observing pins is not a live probe."""
import asyncio
import pytest
from backend import app

@pytest.mark.parametrize('configured', [False,True])
def test_general_status_reuses_coding_readiness_without_claiming_live_health(monkeypatch,configured):
    async def local():return {'available':False}
    async def media():return {'vision':{'available':False},'stt':{'available':False}}
    async def voices():return []
    monkeypatch.setattr(app,'settings',lambda:{'mode':'local'})
    monkeypatch.setattr(app.providers,'ollama_status',local)
    monkeypatch.setattr(app.providers,'secret',lambda _:None)
    monkeypatch.setattr(app.media,'media_status',media)
    monkeypatch.setattr(app.voice,'voices',voices)
    value={'available':False,'tested':False,'can_request':configured,
           'reason_en':'Per-run validation required' if configured else 'Runtime not configured'}
    monkeypatch.setattr(app.coding.sandbox,'status',lambda:{'execution':value})
    result=asyncio.run(app.status())['capabilities']['code_execution']
    assert result['can_request'] is configured and result['available'] is False
    assert result['reason']==value['reason_en']
    assert 'no general model shell tool' in result['scope']
