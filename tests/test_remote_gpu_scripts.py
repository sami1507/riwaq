"""The rented-GPU scripts must keep the model port private: loopback on both ends, key-only SSH, pinned host key."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = (ROOT / 'scripts' / 'remote' / 'riwaq-server-setup.sh').read_text(encoding='utf-8')
TUNNEL = (ROOT / 'scripts' / 'windows' / 'riwaq-remote-tunnel.ps1').read_text(encoding='utf-8')


def test_server_binds_loopback_only_and_checks_it():
    assert 'OLLAMA_HOST=127.0.0.1:11434' in SERVER and '0.0.0.0' not in SERVER
    assert 'answers on the non-loopback address' in SERVER and SERVER.index('non-loopback') < SERVER.index('ollama pull')
    assert "--proto '=https'" in SERVER and 'set -euo pipefail' in SERVER
    assert re.search(r'case "\$MODEL" in \*\[!A-Za-z0-9\._:/-\]\*', SERVER)


def test_tunnel_is_loopback_key_only_and_pins_the_host():
    assert "'127.0.0.1:11434:127.0.0.1:11434'" in TUNNEL and 'ExitOnForwardFailure=yes' in TUNNEL
    for option in ('PasswordAuthentication=no', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=accept-new', 'ForwardAgent=no'):
        assert option in TUNNEL
    assert 'StrictHostKeyChecking=no' not in TUNNEL and '-R' not in re.findall(r"'-[A-Z]'", TUNNEL) and "'-g'" not in TUNNEL
    assert 'ValidatePattern' in TUNNEL and all(ord(c) < 128 for c in TUNNEL)
