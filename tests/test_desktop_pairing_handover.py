"""The desktop shell may hand its own window a one-use pairing code; the web content still gets no native commands."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / 'desktop' / 'src-tauri' / 'src' / 'main.rs').read_text(encoding='utf-8')
GATE = (ROOT / 'frontend' / 'src' / 'PairingGate.tsx').read_text(encoding='utf-8')


def test_web_content_still_receives_no_tauri_permissions_or_commands():
    capability = json.loads((ROOT / 'desktop' / 'src-tauri' / 'capabilities' / 'default.json').read_text(encoding='utf-8'))
    assert capability['permissions'] == []
    assert 'tauri::command' not in SHELL and 'invoke_handler' not in SHELL


def test_shell_mints_only_through_the_documented_local_command_and_validates_the_code():
    assert '.args(["--pair-browser", "--show"])' in SHELL
    assert 'code.len() == 43' in SHELL and 'is_ascii_alphanumeric()' in SHELL
    assert 'stderr(Stdio::null())' in SHELL and not re.search(r'print(?:ln)?!\([^;]*code', SHELL)  # the code is never logged


def test_code_travels_only_in_the_fragment_of_the_loopback_address():
    assert 'format!("http://127.0.0.1:8765/#pair={code}")' in SHELL
    assert '?pair=' not in SHELL and 'url.port() == Some(8765)' in SHELL


def test_gate_removes_the_fragment_at_once_and_tries_the_code_a_single_time():
    assert r"/^#pair=([A-Za-z0-9_-]{43})$/.exec(window.location.hash)" in GATE
    taken = GATE.index('handed.current=found[1]')
    assert GATE.index('window.history.replaceState', taken) - taken < 80
    assert "const value=handed.current;handed.current=null;void login(value);" in GATE
    assert not re.search(r'localStorage\.setItem\([^)]*handed', GATE) and 'console.log' not in GATE
