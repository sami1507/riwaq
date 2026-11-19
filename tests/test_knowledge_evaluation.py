import asyncio
import socket

import pytest

from scripts.evaluate_knowledge import deny_external_connections


@pytest.mark.asyncio
async def test_async_external_connection_is_rejected_before_tcp():
    loop = asyncio.get_running_loop()
    original_connect, original_sock = loop.create_connection, loop.sock_connect
    with deny_external_connections() as checks:
        with pytest.raises(OSError, match="Evaluation forbids"):
            await asyncio.open_connection("203.0.113.1", 443)
        with pytest.raises(OSError, match="Evaluation forbids"):
            socket.getaddrinfo("example.com", 443)
        assert checks["asyncio_guard"]
        assert checks["external_blocked"] == ["203.0.113.1", "example.com"]
    assert loop.create_connection == original_connect
    assert loop.sock_connect == original_sock


@pytest.mark.asyncio
async def test_async_loopback_is_observed_and_guard_restores_after_error(monkeypatch):
    loop = asyncio.get_running_loop()
    observed = []
    async def local_stub(factory, host=None, port=None, *args, **kwargs):
        observed.append((host, port))
        return "transport", "protocol"
    monkeypatch.setattr(loop, "create_connection", local_stub)
    with pytest.raises(RuntimeError, match="simulated"):
        with deny_external_connections() as checks:
            assert await loop.create_connection(lambda: None, "127.0.0.1", 11434) == ("transport", "protocol")
            assert checks["loopback_attempts"] > 0
            assert observed == [("127.0.0.1", 11434)]
            raise RuntimeError("simulated")
    assert loop.create_connection is local_stub


@pytest.mark.asyncio
async def test_direct_async_socket_connection_is_guarded():
    loop = asyncio.get_running_loop()
    with socket.socket() as candidate:
        candidate.setblocking(False)
        with deny_external_connections() as checks:
            with pytest.raises(OSError, match="Evaluation forbids"):
                await loop.sock_connect(candidate, ("203.0.113.1", 443))
            assert checks["external_blocked"] == ["203.0.113.1"]
