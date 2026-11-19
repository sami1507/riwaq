"""Build declarations and fixed dispatch, not evidence of a real EXE launch."""
import json
import hashlib
import sys
from types import SimpleNamespace
import pytest
from backend import launcher, packaged_workers


def runtime(tmp_path):
    return SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path), executable='candidate.exe')


@pytest.mark.parametrize('declaration', [None, [], {}, {'version': 'old'},
    {'version': packaged_workers.VERSION, 'dispatch': 'fixed-v1', 'workers': ['checker']}])
def test_incomplete_build_declaration_never_enables_workers(tmp_path, declaration):
    (tmp_path/'packaged-workers.json').write_text(json.dumps(declaration))
    assert not packaged_workers.bundled('checker', runtime(tmp_path))
    with pytest.raises(ValueError): packaged_workers.fixed_command('checker', runtime(tmp_path))


def test_manifest_only_dispatches_fixed_reviewed_names(tmp_path):
    (tmp_path/'packaged-workers.json').write_text(json.dumps({
        'version': packaged_workers.VERSION, 'dispatch': 'fixed-v1',
        'workers': sorted(packaged_workers.KINDS)}))
    current = runtime(tmp_path)
    assert packaged_workers.fixed_command('checker', current) == ['candidate.exe', '--fixed-worker', 'checker']
    for unsafe in ['python', '../checker', 'document_page']:
        with pytest.raises(ValueError): packaged_workers.fixed_command(unsafe, current)


def test_worker_dispatch_does_not_initialize_user_profile_or_storage(monkeypatch):
    monkeypatch.setattr(launcher, 'sys', SimpleNamespace(frozen=True, argv=['candidate.exe','--fixed-worker','checker']))
    monkeypatch.delenv('RIWAQ_DATA_DIR', raising=False)
    monkeypatch.delenv('RIWAQ_STATIC_DIR', raising=False)
    def no_profile(*_): pytest.fail('A bounded worker has no service profile')
    monkeypatch.setattr(launcher.Path, 'home', no_profile)
    called=[]
    monkeypatch.setattr(packaged_workers, 'dispatch', lambda kind: called.append(kind))
    launcher.main()
    assert called == ['checker']
    assert 'RIWAQ_DATA_DIR' not in launcher.os.environ
    assert 'RIWAQ_STATIC_DIR' not in launcher.os.environ


def test_voice_worker_dispatch_is_fixed_and_tts_only(monkeypatch):
    calls=[]
    monkeypatch.setattr(packaged_workers,'bundled',lambda kind:kind=='voice_tts')
    monkeypatch.setitem(sys.modules,'backend.voice_worker',SimpleNamespace(main=lambda args,**kw:calls.append((args,kw))))
    arguments=['--mode','voices','--input','job/input.json','--output','job']
    packaged_workers.dispatch('voice_tts',arguments)
    assert calls==[(arguments,{'tts_only':True})]
    with pytest.raises(SystemExit):packaged_workers.dispatch('voice_stt',arguments)


def test_packaged_source_identity_uses_build_hashes_not_missing_py_files(tmp_path):
    paths=('backend/voice_audio.py','backend/voice_worker.py','backend/isolated_audio.py','backend/audio_isolation.py')
    root=packaged_workers.Path(__file__).resolve().parents[1]
    hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    manifest={'version':packaged_workers.VERSION,'dispatch':'fixed-v1','workers':sorted(packaged_workers.KINDS),'source_sha256':hashes}
    path=tmp_path/'packaged-workers.json';path.write_text(json.dumps(manifest))
    from backend.voice_audio import implementation_fingerprint
    assert packaged_workers.source_fingerprint(paths,runtime(tmp_path))==implementation_fingerprint()
    manifest['source_sha256'][paths[0]]='unknown';path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):packaged_workers.source_fingerprint(paths,runtime(tmp_path))
