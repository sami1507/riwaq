"""Bounded local-provider admission and retries; never grants execution authority.

The coordinator is process-local. It imposes application resource limits, not a
guessed vendor quota. Cancellation and an absolute deadline cover queue/backoff.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import threading
import time
import httpx


class RuntimeLimit(ValueError):
    pass


def retry_after(value, wall=time.time):
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                return None
            seconds = dt.timestamp() - wall()
        except (ValueError, TypeError, OverflowError):
            return None
    if not (0 <= seconds < float('inf')):
        return None
    return seconds


def failure_kind(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return 'rate_limited'
        if code in {502, 503, 504}:
            return 'temporarily_unavailable'
        if code in {401, 403}:
            return 'authorization_required'
        if 400 <= code < 500:
            return 'invalid_request'
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return 'connection_unavailable'
    if isinstance(exc, (httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.WriteTimeout)):
        return 'outcome_unknown'
    return 'invalid_response'


def replay_allowed(exc, replay='read', output_started=False):
    if output_started or replay == 'never':
        return False
    # A write with an unknown outcome needs an operation-status lookup, not this
    # retry helper. Even read timeouts do not automatically restart generation.
    return failure_kind(exc) in {'rate_limited', 'temporarily_unavailable', 'connection_unavailable'} and replay in {'read', 'idempotent'}


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_seconds: float = .2
    maximum_delay: float = 2.0

    def __post_init__(self):
        if not 1 <= self.attempts <= 5 or not 0 <= self.base_seconds <= self.maximum_delay <= 30:
            raise ValueError('invalid_retry_policy')

    def delay(self, attempt, exc, random_value=random.random, wall=time.time):
        requested = retry_after(exc.response.headers.get('retry-after'), wall) if isinstance(exc, httpx.HTTPStatusError) else None
        exponential = min(self.maximum_delay, self.base_seconds * 2 ** max(0, attempt - 1))
        jitter = exponential * (.5 + .5 * random_value())
        return max(jitter, requested or 0)


def check_deadline(deadline, cancelled=None, clock=time.monotonic):
    if cancelled and cancelled():
        raise asyncio.CancelledError()
    if clock() >= deadline:
        raise RuntimeLimit('provider_deadline_exceeded')


async def wait_bounded(seconds, deadline, *, cancelled=None, clock=time.monotonic, sleep=asyncio.sleep):
    check_deadline(deadline, cancelled, clock)
    target = clock() + seconds
    if target >= deadline:
        raise RuntimeLimit('retry_after_exceeds_deadline')
    while clock() < target:
        check_deadline(deadline, cancelled, clock)
        await sleep(min(.1, target - clock()))
    check_deadline(deadline, cancelled, clock)


async def retry_call(call, *, deadline, policy=RetryPolicy(), replay='read', cancelled=None,
                     clock=time.monotonic, sleep=asyncio.sleep, random_value=random.random, on_retry=None):
    for attempt in range(1, policy.attempts + 1):
        check_deadline(deadline, cancelled, clock)
        try:
            async with asyncio.timeout(max(.001,deadline-clock())):
                result = await call()
            check_deadline(deadline, cancelled, clock)
            return result
        except httpx.HTTPError as exc:
            if attempt == policy.attempts or not replay_allowed(exc, replay):
                raise
            delay = policy.delay(attempt, exc, random_value)
            if on_retry:
                on_retry({'attempt': attempt, 'reason': failure_kind(exc), 'wait_seconds': delay})
            await wait_bounded(delay, deadline, cancelled=cancelled, clock=clock, sleep=sleep)


class Admission:
    """FIFO eligible admissions, per-owner cap, bounded queue and shared cooldown."""
    def __init__(self, capacity=2, per_owner=1, queue_limit=32, owner_queue_limit=4, spacing=.05):
        self.capacity, self.per_owner = capacity, per_owner
        self.queue_limit, self.owner_queue_limit, self.spacing = queue_limit, owner_queue_limit, spacing
        self.lock = threading.Lock()
        self.waiting = []
        self.active = {}
        self.next_start = 0.0
        self.blocked_until = 0.0
        self.completed = 0

    def defer(self, seconds, clock=time.monotonic):
        with self.lock:
            self.blocked_until = max(self.blocked_until, clock() + max(0, seconds))

    def snapshot(self, clock=time.monotonic):
        with self.lock:
            return {'scope': 'single_process_local_provider', 'active': sum(self.active.values()),
                    'waiting': len(self.waiting), 'capacity': self.capacity, 'per_owner': self.per_owner,
                    'cooldown_seconds': round(max(0, self.blocked_until - clock()), 3),
                    'completed_admissions': self.completed, 'provider_quota': 'unknown'}

    def cooldown(self,clock=time.monotonic):
        with self.lock:
            return max(0,self.blocked_until-clock())

    @asynccontextmanager
    async def acquire(self, owner, deadline, *, cancelled=None, clock=time.monotonic, sleep=asyncio.sleep):
        ticket = object()
        entered = False
        check_deadline(deadline, cancelled, clock)
        with self.lock:
            if len(self.waiting) >= self.queue_limit or sum(x[1] == owner for x in self.waiting) >= self.owner_queue_limit:
                raise RuntimeLimit('provider_queue_full')
            self.waiting.append((ticket, owner, deadline))
        try:
            while not entered:
                check_deadline(deadline, cancelled, clock)
                with self.lock:
                    eligible = next((x for x in self.waiting if x[2] > clock() and self.active.get(x[1], 0) < self.per_owner), None)
                    if eligible and eligible[0] is ticket and sum(self.active.values()) < self.capacity and clock() >= max(self.blocked_until, self.next_start):
                        self.waiting.remove(eligible)
                        self.active[owner] = self.active.get(owner, 0) + 1
                        self.next_start = clock() + self.spacing
                        entered = True
                if not entered:
                    await sleep(min(.05, max(.001, deadline - clock())))
            check_deadline(deadline, cancelled, clock)
            yield
        finally:
            with self.lock:
                self.waiting = [x for x in self.waiting if x[0] is not ticket]
                if entered:
                    self.active[owner] -= 1
                    if not self.active[owner]:
                        del self.active[owner]
                    self.completed += 1


LOCAL_ADMISSION = Admission()
