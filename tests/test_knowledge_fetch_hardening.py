"""Transport contract tests with real HTTP parsing and no network requests."""
import http.client
import io
import socket
import ssl
import threading
import time

import pytest

from backend import knowledge_fetch as fetch


def install_wire(monkeypatch, wire):
    requests, connections = [], []
    class RawSocket:
        def makefile(self, *args, **kwargs): return io.BytesIO(wire)
    class Connection:
        sock = None
        def __init__(self, host, address, timeout):
            self.closed = False
            connections.append(self)
        def request(self, method, path, headers): requests.append((method, path, headers))
        def getresponse(self):
            response = http.client.HTTPResponse(RawSocket())
            response.begin()
            return response
        def close(self): self.closed = True
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    return requests, connections


def get(**kwargs):
    return fetch.fetch_https("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"], **kwargs)


def test_short_content_length_is_not_accepted_as_complete(monkeypatch):
    _, connections = install_wire(monkeypatch, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort")
    charged = []
    with pytest.raises(fetch.FetchError, match="Content-Length"):
        get(charge=charged.append)
    assert sum(charged) == 5 and all(c.closed for c in connections)


@pytest.mark.parametrize("headers", [
    b"Content-Length: 3\r\nContent-Length: 5\r\n",
    b"Content-Length: -1\r\n", b"Content-Length: abc\r\n", b"Content-Length: 3,3\r\n",
    b"Transfer-Encoding: chunked\r\nContent-Length: 5\r\n",
    b"Transfer-Encoding: gzip,chunked\r\n",
    b"Content-Encoding: gzip\r\nContent-Encoding: identity\r\n",
    b"Content-Encoding: gzip\r\n", b"Content-Encoding: br\r\n",
])
def test_ambiguous_framing_and_compression_rejected_before_body(monkeypatch, headers):
    requests, connections = install_wire(monkeypatch, b"HTTP/1.1 200 OK\r\n" + headers + b"\r\nabc")
    charged = []
    with pytest.raises(fetch.FetchError): get(charge=charged.append)
    assert charged == [] and all(c.closed for c in connections)
    assert requests[0][2]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc",
    b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nabc",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n",
])
def test_complete_supported_framing_preserves_exact_bytes(monkeypatch, wire):
    _, connections = install_wire(monkeypatch, wire)
    charged = []
    assert get(charge=charged.append).body == b"abc"
    assert sum(charged) == 3 and all(c.closed for c in connections)


def test_truncated_chunk_is_rejected(monkeypatch):
    install_wire(monkeypatch, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\nabc")
    with pytest.raises(fetch.FetchError): get()


def test_deadline_checked_after_read_and_received_bytes_still_charged(monkeypatch):
    stop = threading.Event()
    stop.deadline = time.monotonic() + 60
    charged, closed = [], []
    class Reply:
        status = 200
        length = 1
        def getheaders(self): return [("Content-Length", "1")]
        def read1(self, amount):
            stop.deadline = time.monotonic() - 1
            self.length = 0
            return b"a"
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Reply()
        def close(self): closed.append(True)
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    with pytest.raises(fetch.FetchCancelled): get(cancelled=stop, charge=charged.append)
    assert charged == [1] and closed == [True] and not stop.is_set()


def test_expired_deadline_does_not_dispatch_dns(monkeypatch):
    stop = threading.Event(); stop.deadline = time.monotonic() - 1
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: pytest.fail("Expired request must not resolve"))
    with pytest.raises(fetch.FetchCancelled): get(cancelled=stop)


@pytest.mark.parametrize("cancelled", [False, True])
def test_socket_closure_preserves_cancel_classification(monkeypatch, cancelled):
    stop = threading.Event()
    closed = []
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs):
            if cancelled: stop.set()
            raise OSError("socket was closed")
        def close(self): closed.append(True)
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    with pytest.raises(fetch.FetchError) as exc: get(cancelled=stop)
    assert isinstance(exc.value, fetch.FetchCancelled) is cancelled
    assert closed == [True]


def test_stalled_dns_threads_are_bounded_and_slots_return_after_completion(monkeypatch):
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(fetch, "_DNS_SLOTS", slots)
    release, complete = threading.Event(), threading.Event()
    count, finished = [], []
    def stalled(*args, **kwargs):
        count.append(True)
        try:
            assert release.wait(2)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        finally:
            finished.append(True)
            if len(finished) == 2: complete.set()
    monkeypatch.setattr(fetch.socket, "getaddrinfo", stalled)
    try:
        for _ in range(2):
            with pytest.raises(fetch.FetchError, match="timed out"):
                fetch.resolve_public("docs.example.org", timeout=.01)
        with pytest.raises(fetch.FetchError, match="capacity"):
            fetch.resolve_public("docs.example.org", timeout=.01)
        assert len(count) == 2
    finally:
        release.set()
        assert complete.wait(2)
    # Wait for the resolver finally blocks, not just the mocked DNS return.
    assert slots.acquire(timeout=1) and slots.acquire(timeout=1)
    slots.release(); slots.release()


@pytest.mark.parametrize("url", ["https://[invalid/", "https://@docs.example.org/", "https://user@docs.example.org/"])
def test_malformed_authority_is_a_fetch_error(url):
    with pytest.raises(fetch.FetchError): fetch.normalize_url(url)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, fetch.MAX_FILE_BYTES + 1])
def test_invalid_byte_limit_never_dispatches(monkeypatch, limit):
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: pytest.fail("Invalid limit must not resolve"))
    with pytest.raises(fetch.FetchError): get(max_bytes=limit)


def test_pinned_tls_keeps_hostname_verification_and_sni(monkeypatch):
    connection = fetch.PinnedHTTPSConnection("docs.example.org", "8.8.8.8")
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname is True
    calls = []
    raw = object()
    class Context:
        def wrap_socket(self, value, server_hostname):
            assert value is raw
            calls.append(("sni", server_hostname))
            return "tls-socket"
    def connect(address, timeout):
        calls.append(("connect", address))
        return raw
    connection._context = Context()
    monkeypatch.setattr(fetch.socket, "create_connection", connect)
    connection.connect()
    assert connection.sock == "tls-socket"
    assert calls == [("connect", ("8.8.8.8", 443)), ("sni", "docs.example.org")]


def test_request_counter_covers_redirects_and_prevents_excess_get(monkeypatch):
    class Budget(threading.Event):
        def __init__(self, maximum):
            super().__init__(); self.maximum = maximum; self.requests = 0
        def count_request(self):
            if self.requests >= self.maximum: raise fetch.FetchError("request budget exhausted")
            self.requests += 1
    sent, closed = [], []
    class Reply:
        length = 0
        def __init__(self, path): self.status = 302 if path.endswith("/a") else 200
        def getheaders(self): return [("location", "/docs/b")] if self.status == 302 else [("content-length", "0")]
        def read1(self, amount): return b""
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, headers): self.path = path; sent.append(path)
        def getresponse(self): return Reply(self.path)
        def close(self): closed.append(True)
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    limited = Budget(1)
    with pytest.raises(fetch.FetchError, match="request budget"):
        get(cancelled=limited)
    assert limited.requests == 1 and sent == ["/docs/a"] and len(closed) == 2
    sent.clear(); closed.clear()
    allowed = Budget(2)
    assert get(cancelled=allowed).status == 200
    assert allowed.requests == 2 and sent == ["/docs/a", "/docs/b"] and len(closed) == 2
