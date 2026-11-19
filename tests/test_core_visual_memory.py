"""An image job frees the chat models held by the local runtime before its worker starts.

Found live on Windows: the image worker died with MemoryError while Ollama still held two
chat models. The release is best effort, local only, and never raises."""
import json

import httpx

from backend import providers


def _client(handler):
    transport = httpx.MockTransport(handler)
    real = httpx.Client

    class Local(real):
        def __init__(self, *args, **kwargs):
            kwargs['transport'] = transport
            super().__init__(*args, **kwargs)
    return Local


def test_core_release_local_models_unloads_each_loaded_model(monkeypatch):
    calls = []

    def handler(request):
        assert request.url.host == '127.0.0.1' and request.url.port == 11434
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': [{'name': 'chat:one'}, {'name': 'vision:two'}, {'name': ''}, {}]})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'done': True})
    monkeypatch.setattr(providers.httpx, 'Client', _client(handler))
    assert providers.release_local_models() == ['chat:one', 'vision:two']
    assert calls == [{'model': 'chat:one', 'keep_alive': 0}, {'model': 'vision:two', 'keep_alive': 0}]


def test_core_release_local_models_never_raises_when_the_runtime_is_absent(monkeypatch):
    def handler(request):
        raise httpx.ConnectError('refused')
    monkeypatch.setattr(providers.httpx, 'Client', _client(handler))
    assert providers.release_local_models() == []


def test_core_image_generation_releases_models_but_rendering_does_not():
    from pathlib import Path
    source = (Path(providers.__file__).parent / 'visual_jobs.py').read_text(encoding='utf-8')
    block = source[source.index('released=[]'):source.index('with tempfile.TemporaryDirectory')]
    assert "if r['kind']!='render':" in block and 'providers.release_local_models()' in block
