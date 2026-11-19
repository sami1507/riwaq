"""Bounded HTTPS retrieval pinned to validated public DNS addresses.

No ambient proxy, cookies, credentials, JavaScript execution, or automatic crawl.
Every redirect is validated against the same approved hosts and path scopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from email.utils import parsedate_to_datetime
import http.client
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "Riwaq/0.1 (local knowledge pilot; interactive operator)"
MAX_REDIRECTS = 4
MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_LIMITS = {"max_documents": 100, "max_sources": 5, "download_bytes": 1024 ** 3, "file_bytes": MAX_FILE_BYTES, "run_seconds": 1800, "concurrent_domains": 1}
# OS getaddrinfo cannot be interrupted portably. Keep stalled daemon resolvers
# bounded instead of creating an unbounded new thread on every timed-out fetch.
_DNS_SLOTS = threading.BoundedSemaphore(4)


def load_limits(path):
    """Operator configuration, read once at startup; malformed limits fail closed."""
    configured = json.loads(path.read_text(encoding="utf-8")).get("limits", {}) if path.exists() else {}
    if not isinstance(configured, dict) or set(configured) - set(DEFAULT_LIMITS):
        raise ValueError("Knowledge limits must contain only the documented limit fields")
    limits = DEFAULT_LIMITS | configured
    ceilings = DEFAULT_LIMITS | {"max_documents": 1000}
    for key, value in limits.items():
        if type(value) is not int or not 1 <= value <= ceilings[key]:
            raise ValueError(f"Knowledge limit {key} must be an integer from 1 to {ceilings[key]}")
    return limits


class FetchError(Exception):
    pass


class FetchCancelled(FetchError):
    pass


class HostPacer:
    """One shared scheduler for sequential requests, including metadata/redirects.

    Intervals begin at response completion, so a slow TLS/body transfer cannot
    compress the following request gap. No automatic retries are introduced.
    """
    def __init__(self, *, clock=None, wall_clock=None, wait=None):
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.wait = wait or self._wait
        self.hosts = OrderedDict()
        self.guard = threading.Lock()

    @staticmethod
    def _wait(seconds, cancelled):
        if cancelled is not None:
            return cancelled.wait(seconds)
        time.sleep(seconds)
        return False

    def _state(self, host):
        if host not in self.hosts:
            if len(self.hosts) >= 1024:
                # Never evict an active server cooldown to make room.
                removable = next((key for key, item in self.hosts.items() if max(item["retry_at"], item["last"] + item["delay"]) <= self.clock()), None)
                if removable is None:
                    raise FetchError("Host pacing capacity reached; wait for existing cooldowns")
                del self.hosts[removable]
            self.hosts[host] = {"delay": 0.0, "last": float("-inf"), "retry_at": 0.0}
        self.hosts.move_to_end(host)
        return self.hosts[host]

    def set_delay(self, host, seconds):
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise FetchError("Invalid host delay")
        with self.guard:
            item = self._state(host)
            item["delay"] = max(item["delay"], seconds)

    def acquire(self, host, cancelled=None):
        while True:
            current = self.clock()
            deadline = getattr(cancelled, "deadline", float("inf"))
            if (cancelled is not None and cancelled.is_set()) or current >= deadline:
                raise FetchCancelled("Request cancelled while waiting for host rate limit")
            with self.guard:
                item = self._state(host)
                due = max(item["retry_at"], item["last"] + item["delay"])
                if due <= current:
                    item["last"] = current
                    return
            if self.wait(min(due - current, deadline - current), cancelled):
                raise FetchCancelled("Request cancelled while waiting for host rate limit")

    def finish(self, host):
        with self.guard:
            self._state(host)["last"] = self.clock()

    def defer_retry_after(self, host, value):
        if not isinstance(value, str) or len(value) > 256:
            return False
        value = value.strip()
        try:
            if value.isascii() and value.isdigit():
                seconds = int(value)
            else:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    return False
                seconds = max(0, parsed.timestamp() - self.wall_clock())
            if not math.isfinite(seconds):
                return False
        except (ValueError, TypeError, OverflowError):
            return False
        with self.guard:
            item = self._state(host)
            item["retry_at"] = max(item["retry_at"], self.clock() + seconds)
        return True


@dataclass
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


def normalize_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096 or any(ord(char) < 32 for char in url) or "\\" in url:
        raise FetchError("Invalid URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError) as exc:
        raise FetchError("Invalid URL host") from exc
    if parsed.scheme.lower() != "https" or not host or "@" in parsed.netloc or port not in (None, 443):
        raise FetchError("Only credential-free HTTPS URLs on port 443 are permitted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise FetchError("Use an approved DNS hostname, not an IP-literal URL")
    decoded_path = parsed.path or "/"
    for _ in range(5):
        next_path = unquote(decoded_path)
        if any(ord(char) < 32 for char in next_path) or any(part in {".", ".."} for part in next_path.replace("\\", "/").split("/")) or "\\" in next_path:
            raise FetchError("URL path traversal or encoded controls are not permitted")
        if next_path == decoded_path:
            break
        decoded_path = next_path
    else:
        raise FetchError("Excessively encoded paths are not permitted")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def validate_scope(url: str, allowed_hosts, allowed_prefixes=()):
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    hosts = {str(host).lower().rstrip(".") for host in allowed_hosts}
    if parsed.hostname not in hosts:
        raise FetchError("URL host is outside the approved source allowlist")
    if allowed_prefixes:
        permitted = False
        for prefix in allowed_prefixes:
            if str(prefix).startswith("https://"):
                item = urlsplit(normalize_url(prefix))
                if item.hostname != parsed.hostname:
                    continue
                prefix_path = unquote(item.path)
            else:
                prefix_path = unquote(str(prefix))
            path = unquote(parsed.path)
            if prefix_path == "/" or path == prefix_path or path.startswith(prefix_path.rstrip("/") + "/"):
                permitted = True
                break
        if not permitted:
            raise FetchError("URL path is outside the approved source scopes")
    return normalized


def resolve_public(host: str, timeout=8):
    # A daemon resolver gives the request a deadline even when the OS resolver stalls.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchError("DNS resolver capacity reached; existing requests are still pending")
    slots = _DNS_SLOTS
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.put(exc)
        finally:
            slots.release()
    try:
        threading.Thread(target=resolve, daemon=True).start()
    except Exception:
        slots.release()
        raise FetchError("DNS resolver could not start") from None
    try:
        answers = result.get(timeout=max(.01, timeout))
    except queue.Empty as exc:
        raise FetchError("DNS resolution timed out") from exc
    if isinstance(answers, Exception):
        raise FetchError("DNS resolution failed") from answers
    try:
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    except (TypeError, IndexError):
        raise FetchError("DNS returned an invalid address response") from None
    if not addresses:
        raise FetchError("DNS returned no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise FetchError("DNS returned an invalid address") from None
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
            raise FetchError("Source DNS resolves to a non-public address")
        if getattr(ip, "ipv4_mapped", None) is not None:
            raise FetchError("IPv4-mapped IPv6 addresses are not accepted")
        if getattr(ip, "sixtofour", None) is not None or getattr(ip, "teredo", None) is not None:
            raise FetchError("IPv6 transition addresses are not accepted")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout=12):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def fetch_https(url, *, allowed_hosts, allowed_prefixes=(), max_bytes=MAX_FILE_BYTES,
                conditional=None, charge=None, cancelled=None, remaining_budget=None, pacer=None,
                method="GET", body=None, follow_redirects=True):
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_FILE_BYTES:
        raise FetchError("Per-file byte limit must be a positive bounded integer")
    # The sole write transport is an explicitly authorized bounded static form.
    # It never replays a body across a redirect or retries an unknown result.
    if method not in {"GET", "POST"} or type(follow_redirects) is not bool:
        raise FetchError("Unsupported HTTP method or redirect policy")
    if method == "POST":
        if not isinstance(body, bytes) or not 1 <= len(body) <= 32768 or follow_redirects or conditional:
            raise FetchError("Form POST requires a bounded body and no redirects or conditional replay")
    elif body is not None:
        raise FetchError("GET request bodies are not permitted")
    def checkpoint():
        if cancelled is not None and (cancelled.is_set() or time.monotonic() >= getattr(cancelled, "deadline", float("inf"))):
            raise FetchCancelled("Download cancelled or deadline exceeded")
    pacer = pacer or getattr(cancelled, "pacer", None)
    current = validate_scope(url, allowed_hosts, allowed_prefixes)
    for redirect_number in range(MAX_REDIRECTS + 1):
        checkpoint()
        current = validate_scope(current, allowed_hosts, allowed_prefixes)
        parsed = urlsplit(current)
        if pacer is not None:
            pacer.acquire(parsed.hostname, cancelled)
        remaining_time = max(.01, getattr(cancelled, "deadline", time.monotonic() + 12) - time.monotonic())
        addresses = resolve_public(parsed.hostname, timeout=min(8, remaining_time))
        checkpoint()
        remaining_time = max(.01, getattr(cancelled, "deadline", time.monotonic() + 12) - time.monotonic())
        connection = PinnedHTTPSConnection(parsed.hostname, addresses[0], timeout=min(12, remaining_time))
        if cancelled is not None and hasattr(cancelled, "attach"):
            cancelled.attach(connection)
        try:
            checkpoint()
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "text/html,text/plain,text/markdown,application/json,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document;q=0.8", "Connection": "close"}
            if conditional:
                for key in ("If-None-Match", "If-Modified-Since"):
                    value = conditional.get(key)
                    if value and len(value) < 2048 and "\n" not in value and "\r" not in value:
                        headers[key] = value
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            if cancelled is not None and callable(getattr(cancelled, "count_request", None)):
                cancelled.count_request()
            checkpoint()
            if method == "POST":
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
                connection.request("POST", path, body=body, headers=headers)
            else:
                connection.request("GET", path, headers=headers)
            checkpoint()
            response = connection.getresponse()
            checkpoint()
            fields = response.getheaders()
            metadata = {key.lower(): value for key, value in fields}
            # http.client may use the first framing header while a dict retains
            # the last. Do not accept an ambiguous representation of the body.
            for name in ("content-length", "transfer-encoding", "content-encoding"):
                if sum(key.lower() == name for key, _ in fields) > 1:
                    raise FetchError("Duplicate HTTP body framing or encoding headers are not accepted")
            if "transfer-encoding" in metadata:
                if metadata["transfer-encoding"].strip().lower() != "chunked" or "content-length" in metadata:
                    raise FetchError("Ambiguous or unsupported HTTP transfer framing")
            if pacer is not None and metadata.get("retry-after"):
                pacer.defer_retry_after(parsed.hostname, metadata["retry-after"])
            if follow_redirects and response.status in (301, 302, 303, 307, 308):
                if redirect_number == MAX_REDIRECTS or not metadata.get("location"):
                    raise FetchError("Redirect limit exceeded or location missing")
                current = validate_scope(urljoin(current, metadata["location"]), allowed_hosts, allowed_prefixes)
                if pacer is not None and metadata.get("retry-after"):
                    pacer.defer_retry_after(urlsplit(current).hostname, metadata["retry-after"])
                conditional = None
                continue
            if response.status == 304:
                return FetchResult(current, 304, metadata, b"")
            if metadata.get("content-encoding", "identity").lower() not in ("", "identity"):
                raise FetchError("Compressed HTTP transfer is not accepted; uncompressed byte limits must remain enforceable")
            length = metadata.get("content-length")
            if length is not None and (not length.strip().isascii() or not length.strip().isdigit() or len(length.strip()) > 20):
                raise FetchError("Invalid HTTP Content-Length")
            announced = int(length.strip()) if length is not None else 0
            if announced > max_bytes:
                raise FetchError("Response exceeds the approved per-file limit")
            chunks, received = [], 0
            while True:
                checkpoint()
                remaining = max_bytes - received
                allowance = min(65536, remaining + 1)
                if remaining_budget:
                    allowance = min(allowance, remaining_budget())
                    if allowance <= 0:
                        if response.length == 0:
                            break
                        raise FetchError("Cumulative download budget exhausted")
                if connection.sock is not None and hasattr(cancelled, "deadline"):
                    connection.sock.settimeout(max(.01, min(12, cancelled.deadline - time.monotonic())))
                block = response.read1(allowance)
                if not block:
                    checkpoint()
                    if getattr(response, "length", None) not in (None, 0):
                        raise FetchError("Response ended before the declared Content-Length")
                    break
                if charge:
                    charge(len(block))  # Charge every received body byte, including failed/incomplete downloads.
                checkpoint()
                received += len(block)
                if received > max_bytes:
                    raise FetchError("Response exceeded the approved per-file limit")
                chunks.append(block)
            return FetchResult(current, response.status, metadata, b"".join(chunks))
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            # Closing a live socket is how JobControl interrupts blocking I/O.
            # Preserve cancellation instead of recording that closure as failure.
            checkpoint()
            raise FetchError("HTTPS fetch failed or timed out") from exc
        finally:
            connection.close()
            if pacer is not None:
                pacer.finish(parsed.hostname)
            if cancelled is not None and hasattr(cancelled, "detach"):
                cancelled.detach(connection)
    raise FetchError("Redirect limit exceeded")


def robots_policy(url, *, allowed_hosts, charge=None, cancelled=None, remaining_budget=None, pacer=None, max_file_bytes=MAX_FILE_BYTES):
    pacer = pacer or getattr(cancelled, "pacer", None)
    parsed = urlsplit(normalize_url(url))
    robots_url = f"https://{parsed.hostname}/robots.txt"
    response = fetch_https(robots_url, allowed_hosts=allowed_hosts, max_bytes=min(512 * 1024, max_file_bytes), charge=charge, cancelled=cancelled, remaining_budget=remaining_budget, pacer=pacer)
    if response.status == 404:
        return {"allowed": True, "delay_seconds": None, "robots_url": robots_url, "status": 404}
    if response.status != 200:
        raise FetchError(f"robots.txt could not be verified (HTTP {response.status}); fetch denied")
    text = response.body.decode("utf-8", errors="replace")
    if "<html" in text[:1000].lower() or "<!doctype html" in text[:1000].lower():
        raise FetchError("robots.txt returned HTML; policy is unverified")
    parser = RobotFileParser(robots_url)
    # urllib accepts integer Crawl-delay only. Round valid decimal extensions
    # upward, preserving the same user-agent matching and never requesting faster.
    lines = []
    for line in text.splitlines():
        match = re.match(r"\s*crawl-delay\s*:\s*(\d+(?:\.\d+)?)\s*(?:#.*)?$", line, flags=re.I)
        if match and "." in match[1]:
            if len(match[1]) > 64:
                raise FetchError("robots.txt Crawl-delay is too large to interpret")
            line = "Crawl-delay: " + str(math.ceil(float(match[1])))
        lines.append(line)
    parser.parse(lines)
    delay = parser.crawl_delay(USER_AGENT)
    if pacer is not None and delay is not None:
        pacer.set_delay(parsed.hostname, delay)
    return {"allowed": parser.can_fetch(USER_AGENT, url), "delay_seconds": delay, "robots_url": robots_url, "status": 200}
