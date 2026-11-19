"""Run the fixed local AI-core evidence profile; never imports old result files.

Exit0 requires all100 criteria passed in their declared scope,1 means a failed
test/criterion,2 means incomplete evidence. No deployment or release approval.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def new_output(value):
    path = Path(value).absolute()
    allowed = (ROOT / 'reports').resolve()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('linked_evaluation_output')
    if not path.resolve().is_relative_to(allowed) or path.exists():
        raise ValueError('new_report_directory_required')
    path.mkdir(parents=True)
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def finish_report(output, dataset, dataset_hash, source, configuration, execution):
    """Internal handoff from a freshly executed fixed CLI profile, never HTTP."""
    from backend.ai_core_evaluation import read_junit, assess_case, independent_sample, totals, RELATED, EXACT
    from backend.skill_release import current_source_fingerprint
    config_hash = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    observed = read_junit(output / 'pytest.xml')
    cases = [assess_case(case, observed, execution_ok=execution['exit_code'] in (0, 1)) for case in dataset['cases']]
    sample = independent_sample()
    changed = current_source_fingerprint(ROOT) != source
    counts = totals(cases)
    failed = counts['failed'] or execution['status'] == 'failed' or sample['totals']['failed']
    result = {'schema_version': 'riwaq-ai-core-evaluation-1', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'dataset': {'version': dataset['version'], 'sha256': dataset_hash, 'cases': 100, 'heldout': False},
        'source_fingerprint': source, 'source_changed_during_run': changed,
        'configuration': configuration, 'configuration_sha256': config_hash, 'execution': execution,
        'cases': cases, 'totals': counts, 'independent_sample': sample,
        'critical_failures': [case['id'] for case in cases if case['critical'] and case['status'] == 'failed'],
        'status': 'failed' if failed else 'blocked' if changed or execution['status'] == 'blocked' else 'passed' if counts['passed'] == 100 else 'partial',
        'missing_bindings': [case['id'] for case in dataset['cases'] if case['id'] not in EXACT and case['id'] not in RELATED],
        'scope': 'Fresh deterministic components and synthetic API/mock contracts. Exact component passes do not certify conversational interpretation or entire product capability.',
        'unexecuted_profiles': {'live_model': 'not_run', 'semantic_independent_review': 'not_run', 'native_desktop': 'not_run', 'visual_human_review': 'not_run'},
        'historical_results_imported': False, 'release_approved': False, 'published': False}
    write_json(output / 'AI_CORE_EVAL_RESULTS.json', result)
    write_json(output / 'run-manifest.json', {'source_fingerprint': source, 'source_changed_during_run': changed,
        'dataset_sha256': dataset_hash, 'configuration_sha256': config_hash,
        'files': [{'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()]})
    return result


def configuration(selectors, *, profile='ai-core-local-v1', timeout=360):
    return {'profile': profile, 'provider': 'none', 'model': None,
        'network_test_policy': 'no live provider, external message or production input in this profile',
        'python': platform.python_version(), 'platform': platform.platform(),
        'timeout_seconds': timeout, 'selectors': list(selectors), 'independent_sample_model_calls': 0}


def run(output):
    from backend.ai_core_evaluation import load_cases, profile_selectors
    from backend.skill_release import current_source_fingerprint
    from scripts.run_skill_regression import invoke
    output = new_output(output)
    dataset, dataset_hash = load_cases()
    source = current_source_fingerprint(ROOT)
    selected = profile_selectors()
    if not selected:
        raise ValueError('approved_ai_contracts_unavailable')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('RIWAQ_', 'OLLAMA_')) and not any(term in k.upper() for term in ('API_KEY', 'PASSWORD', 'SECRET', 'ACCESS_TOKEN'))}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    with tempfile.TemporaryDirectory(prefix='riwaq-ai-core-evaluation-') as temporary:
        env['RIWAQ_DATA_DIR'] = temporary
        argv = [sys.executable, '-m', 'pytest', *selected, '-q', '--junitxml=' + str(output / 'pytest.xml')]
        execution = invoke(argv, ROOT, env, 360, output / 'pytest.log')
    return finish_report(output, dataset, dataset_hash, source, configuration(selected), execution)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='A new directory below this checkout reports/')
    args = parser.parse_args()
    try:
        result = run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, 'AI-core evaluation refused: a valid fixed dataset, unmodified sample and new local reports directory are required.\n')
    print(json.dumps({'status': result['status'], 'totals': result['totals'], 'source_changed_during_run': result['source_changed_during_run'], 'result': str(Path(args.output) / 'AI_CORE_EVAL_RESULTS.json')}, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1 if result['status'] == 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
