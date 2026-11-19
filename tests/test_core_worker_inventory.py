"""GAP-003: the set of places where the service starts another process is reviewed and pinned.

Each entry is classified in docs/WORKER_ISOLATION_MATRIX.md. A new spawn site, or a shell
invocation, fails here until it is reviewed and added to that matrix; this test does not
by itself prove any boundary (tests/test_isolation_probe_live.py does, for the container)."""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
SPAWN = re.compile(r'subprocess\.(?:Popen|run|call|check_output|check_call)\(|create_subprocess_(?:exec|shell)\(|os\.system\(|os\.startfile\(|os\.spawn\w*\(|os\.exec[lv]\w*\(|ProcessPoolExecutor\(|multiprocessing\.Process\(')

REVIEWED = {
    'isolated_worker.py': 'container',        # Docker CLI: the only boundary that takes untrusted bytes or code
    'visual_runner.py': 'host-job-object',    # fixed model workers (image, video, voice) with kill-on-close
    'media.py': 'host-fixed-worker',          # speech-to-text on PCM already decoded in the container
    'voice_audio.py': 'host-fixed-script',    # fixed Windows speech script with validated fields
    'video_render.py': 'host-fixed-script',   # fixed text-layer renderer with a JSON job
    'document_extract.py': 'in-container',    # OCR helper; shipped in the document runner's payload (host use unverified)
    'coding_context.py': 'host-fixed-worker', # syntax-tree parse of source text, never execution
    'reasoning.py': 'host-fixed-worker',      # fixed calculator/checker with a 20 kB JSON input
}


def _sites():
    found = {}
    for source in sorted(BACKEND.glob('*.py')):
        text = source.read_text(encoding='utf-8')
        hits = SPAWN.findall(text)
        if hits:
            found[source.name] = text
    return found


def test_core_every_process_spawn_site_is_reviewed():
    assert set(_sites()) == set(REVIEWED)


def test_core_no_spawn_site_uses_a_shell():
    for name, text in _sites().items():
        assert 'shell=True' not in text, name
        assert 'create_subprocess_shell' not in text, name
        assert 'os.system(' not in text, name


def test_core_matrix_document_lists_every_reviewed_site():
    matrix = (BACKEND.parent / 'docs' / 'WORKER_ISOLATION_MATRIX.md').read_text(encoding='utf-8')
    for name in REVIEWED:
        assert f'`backend/{name}`' in matrix, name


def test_core_every_runner_gets_the_same_container_boundary():
    """All six runners must be created with identical isolation arguments; only the image
    and the fixed entrypoint may differ. The live hostile probes run in two of them."""
    from backend import isolated_worker
    from backend.isolated_bootstrap import RUNNERS

    class Config:
        images = {name: 'sha256:' + format(index, '064x') for index, name in enumerate(sorted(RUNNERS))}
    shapes = {}
    for runner, (executable, bootstrap) in RUNNERS.items():
        args = isolated_worker.create_arguments(Config, runner, 'riwaq-fixture', 512, 30)
        cut = args.index('--entrypoint')
        assert args[cut:] == ['--entrypoint', executable, Config.images[runner], *bootstrap]
        shapes[runner] = tuple(args[:cut])
    assert len(set(shapes.values())) == 1, shapes
    boundary = next(iter(shapes.values()))
    for flag in ('--network', 'none'), ('--user', '65534:65534'), ('--cap-drop', 'ALL'), ('--pull', 'never'), ('--security-opt', 'no-new-privileges=true'):
        index = boundary.index(flag[0])
        assert boundary[index + 1] == flag[1], flag
    assert '--read-only' in boundary and '--privileged' not in boundary
    assert not any(a in boundary for a in ('-v', '--volume', '--mount', '--device', '-p', '--publish'))

