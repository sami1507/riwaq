"""GAP-003 live evidence: hostile code inside the real isolated container.

Opt-in (needs the local Docker worker configuration, no downloads). The code below runs as
the untrusted payload inside the `python_unittest` runner and tries to leave its boundary;
every assertion inside it states what must be impossible. The outer test then checks the
supervisor's own receipt. This proves the boundary for the Docker runners only: workers that
run as limited host processes are not covered and are not operating-system isolation."""
import os
import threading
from pathlib import Path

import pytest

from backend import isolated_worker

CONFIG = os.environ.get('RIWAQ_TEST_ISOLATED_CONFIG')
pytestmark = pytest.mark.skipif(not CONFIG or not Path(CONFIG).is_file(), reason='RIWAQ_TEST_ISOLATED_CONFIG is required for the real container probe')

HOSTILE = r'''
import os, socket, unittest

class Escape(unittest.TestCase):
    def test_no_network_interface_but_loopback(self):
        names = sorted(os.listdir('/sys/class/net'))
        self.assertEqual(names, ['lo'], names)

    def test_tcp_and_dns_egress_fail(self):
        for target in (('1.1.1.1', 443), ('192.168.65.2', 53), ('172.17.0.1', 2375)):
            s = socket.socket(); s.settimeout(3)
            with self.assertRaises(OSError):
                s.connect(target)
            s.close()
        with self.assertRaises(OSError):
            socket.getaddrinfo('docs.python.org', 443)

    def test_identity_is_unprivileged_without_capabilities(self):
        self.assertEqual((os.getuid(), os.getgid()), (65534, 65534))
        with open('/proc/self/status') as handle: status = handle.read()
        caps = {line.split(':')[0]: line.split()[1] for line in status.splitlines() if line.startswith('Cap')}
        self.assertEqual(int(caps['CapEff'], 16), 0, caps)
        self.assertEqual(int(caps['CapPrm'], 16), 0, caps)
        self.assertIn('NoNewPrivs:\t1', status)

    def test_root_filesystem_is_read_only_and_only_tmp_is_writable(self):
        for path in ('/probe', '/usr/probe', '/etc/probe', '/home/probe', '/var/probe'):
            with self.assertRaises(OSError):
                open(path, 'w')
        with open('/tmp/probe', 'w') as handle:
            handle.write('ok')

    def test_no_host_filesystem_or_docker_socket_is_visible(self):
        for path in ('/var/run/docker.sock', '/run/docker.sock', '/host', '/mnt/c', '/mnt/d', '/mnt/host', '/c', '/d', '/run/desktop'):
            self.assertFalse(os.path.exists(path), path)
        with open('/proc/self/mounts') as handle: mounts = handle.read()
        for marker in ('9p', 'drvfs', 'virtiofs', 'fuse.grpcfuse', ' cifs '):
            self.assertNotIn(marker, mounts, marker)

    def test_host_secrets_are_not_in_the_environment(self):
        # GPG_KEY is the public release-signing fingerprint baked into the official Python image.
        leaked = [k for k in os.environ if k != 'GPG_KEY' and any(w in k.upper() for w in ('RIWAQ', 'TOKEN', 'SECRET', 'KEY', 'PASSWORD', 'USERPROFILE', 'APPDATA', 'OLLAMA', 'DOCKER'))]
        self.assertEqual(leaked, [])
        host_paths = [k for k, v in os.environ.items() if ':\\' in v or '\\Users\\' in v or '/mnt/' in v]
        self.assertEqual(host_paths, [])

    def test_new_processes_are_capped(self):
        children = []
        try:
            with self.assertRaises(OSError):
                for _ in range(200):
                    pid = os.fork()
                    if pid == 0:
                        import time; time.sleep(20); os._exit(0)
                    children.append(pid)
        finally:
            import signal
            for pid in children:
                try: os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
                except OSError: pass
        self.assertLess(len(children), 64)
'''


def test_hostile_code_cannot_leave_the_isolated_container():
    config = isolated_worker.load_config(CONFIG)
    receipt = isolated_worker.execute({'test_escape.py': HOSTILE.encode()}, 'python_unittest', ['test_escape.py'],
                                      config=config, stop=threading.Event(), authorize=lambda: True, timeout_seconds=60)
    detail = '%s/%s tests_passed=%s count=%s\n%s' % (receipt.get('status'), receipt.get('reason_code'), receipt.get('tests_passed'), receipt.get('test_count'), ((receipt.get('stderr') or '') + (receipt.get('stdout') or ''))[-6000:])
    # The supervisor reports what it inspected and never claims that untrusted tests are
    # honest (tests_passed and isolation_verified stay unset by design), so the verdict of
    # the hostile suite is read from the runner's own output and exit code.
    assert receipt['executed'] is True and receipt['policy_inspected'] is True, detail
    assert receipt['status'] == 'completed' and receipt['runner_exit_zero'] is True, detail
    assert 'Ran 7 tests' in detail and '\nOK' in detail and 'FAILED' not in detail, detail
    assert receipt['network_policy'] == 'none' and receipt['host_fallback'] is False and receipt['automatic_download'] is False
    assert receipt['cleanup_verified'] is True, detail

def test_revoked_authority_never_starts_the_container():
    config = isolated_worker.load_config(CONFIG)
    receipt = isolated_worker.execute({'test_escape.py': HOSTILE.encode()}, 'python_unittest', ['test_escape.py'],
                                      config=config, stop=threading.Event(), authorize=lambda: False, timeout_seconds=60)
    assert receipt['executed'] is False and receipt['start_attempted'] is False, receipt


HOSTILE_NODE = r'''
const test = require('node:test'), assert = require('node:assert'), fs = require('node:fs'), net = require('node:net'), dns = require('node:dns'), os = require('node:os');

test('only the loopback interface exists', () => {
  assert.deepStrictEqual(fs.readdirSync('/sys/class/net').sort(), ['lo']);
  assert.deepStrictEqual(Object.keys(os.networkInterfaces()).filter(n => n !== 'lo'), []);
});

test('tcp egress fails', async () => {
  for (const [host, port] of [['1.1.1.1', 443], ['192.168.65.2', 53], ['172.17.0.1', 2375]]) {
    await new Promise((resolve, reject) => {
      const s = net.connect({host, port, timeout: 3000});
      s.on('connect', () => { s.destroy(); reject(new Error('connected to ' + host)); });
      s.on('error', () => resolve()); s.on('timeout', () => { s.destroy(); resolve(); });
    });
  }
});

test('dns resolution fails', async () => {
  await assert.rejects(dns.promises.lookup('docs.python.org'));
});

test('identity is unprivileged without capabilities', () => {
  assert.strictEqual(process.getuid(), 65534); assert.strictEqual(process.getgid(), 65534);
  const status = fs.readFileSync('/proc/self/status', 'utf8');
  assert.strictEqual(parseInt(/CapEff:\s*([0-9a-f]+)/.exec(status)[1], 16), 0);
  assert.strictEqual(parseInt(/CapPrm:\s*([0-9a-f]+)/.exec(status)[1], 16), 0);
  assert.match(status, /NoNewPrivs:\s*1/);
});

test('root filesystem is read-only and only /tmp is writable', () => {
  for (const p of ['/probe', '/usr/probe', '/etc/probe', '/home/probe', '/var/probe']) assert.throws(() => fs.writeFileSync(p, 'x'));
  fs.writeFileSync('/tmp/probe', 'ok');
});

test('no host filesystem or docker socket is visible', () => {
  for (const p of ['/var/run/docker.sock', '/run/docker.sock', '/host', '/mnt/c', '/mnt/d', '/mnt/host', '/c', '/d', '/run/desktop']) assert.strictEqual(fs.existsSync(p), false, p);
  const mounts = fs.readFileSync('/proc/self/mounts', 'utf8');
  for (const marker of ['9p', 'drvfs', 'virtiofs', 'fuse.grpcfuse', ' cifs ']) assert.strictEqual(mounts.includes(marker), false, marker);
});

test('host values are not in the environment', () => {
  const leaked = Object.keys(process.env).filter(k => /RIWAQ|TOKEN|SECRET|PASSWORD|USERPROFILE|APPDATA|OLLAMA|DOCKER/i.test(k));
  assert.deepStrictEqual(leaked, []);
  assert.deepStrictEqual(Object.entries(process.env).filter(([k, v]) => v.includes(':\\') || v.includes('/mnt/')).map(([k]) => k), []);
});
'''


def test_hostile_node_code_cannot_leave_the_isolated_container():
    config = isolated_worker.load_config(CONFIG)
    if 'node_test' not in config.images:
        pytest.skip('node_test runner is not configured')
    receipt = isolated_worker.execute({'escape.test.js': HOSTILE_NODE.encode()}, 'node_test', ['escape.test.js'],
                                      config=config, stop=threading.Event(), authorize=lambda: True, timeout_seconds=60)
    detail = '%s/%s\n%s' % (receipt.get('status'), receipt.get('reason_code'), ((receipt.get('stderr') or '') + (receipt.get('stdout') or ''))[-6000:])
    assert receipt['executed'] is True and receipt['policy_inspected'] is True, detail
    assert receipt['status'] == 'completed' and receipt['runner_exit_zero'] is True, detail
    assert 'pass 7' in detail and 'fail 0' in detail, detail
    assert receipt['network_policy'] == 'none' and receipt['host_fallback'] is False and receipt['cleanup_verified'] is True, detail


def test_every_configured_runner_image_is_pinned_and_passes_the_daemon_probe():
    """The four fixed runners take data, not code, so they cannot carry a hostile suite; they
    share the container arguments checked in tests/test_core_worker_inventory.py. Here the
    supervisor's own probe must accept every configured image: pinned by content id, no
    implicit volumes, on a daemon with seccomp and resource controls."""
    config = isolated_worker.load_config(CONFIG)
    seen = {}
    for runner, image in config.images.items():
        assert image.startswith('sha256:') and len(image) == 71, (runner, image)
        evidence = isolated_worker.probe(config, runner)['evidence']
        assert evidence['image_id'] == image and evidence['resource_controls_reported'] is True, (runner, evidence)
        seen[runner] = evidence['image_id'][:19]
    assert seen, 'no runner configured'
    print('probed runners:', seen)

