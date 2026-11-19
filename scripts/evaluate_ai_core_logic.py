"""Five synthetic logic prompts through the installed local provider, one run each.

This records raw answers for semantic review, not a keyword-scored pass claim.
No downloads, external provider, training, tools, or production persistence.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import providers, reasoning

CASES = [
    {'id': 'AI-06-T01', 'prompt': 'كل A هو B، وكل B هو C، وس من A. هل س من C؟ أجب مع تبرير مختصر.', 'criterion': 'Yes, by both given implications; concise valid justification.'},
    {'id': 'AI-06-T02', 'prompt': 'كل مسؤول يقرأ؛ س يقرأ. هل يلزم أن س مسؤول؟ أجب مع تبرير مختصر.', 'criterion': 'Not entailed; reading does not imply responsibility.'},
    {'id': 'AI-06-T03', 'prompt': 'هل يوجد عدد حقيقي x يحقق x > 10 و x < 5 معًا؟ أجب مع تبرير مختصر.', 'criterion': 'No solution; no value satisfying incompatible inequalities is invented.'},
    {'id': 'AI-06-T04', 'prompt': 'رتب A وB وC، كل منها مرة واحدة، بحيث A قبل B وB قبل C. اذكر الترتيب وتحقق من القيدين بإيجاز.', 'criterion': 'A then B then C, once each, both constraints checked.'},
    {'id': 'AI-06-T05', 'prompt': 'لدينا عينة من 5 حالات، نجحت 3 منها. هل هذا يثبت نجاح كل المجتمع؟ أجب مع تبرير مختصر.', 'criterion': 'No universal success conclusion; sample and population distinguished.'},
]
VARIED_CASES = [
    {'id': 'LOGIC-V01', 'prompt': 'فحصنا عينة من 12 جهازًا من مخزن كبير، وكلها سليمة. هل يثبت هذا أن كل جهاز في المخزن سليم؟ ما الذي يمكن استنتاجه فقط؟', 'criterion': 'All sampled devices are good; universal warehouse claim is not proved, and larger samples alone are not universal proof.'},
    {'id': 'LOGIC-V02', 'prompt': 'هذا هو كامل المجتمع: خمسة موظفين، ثلاثة فقط اجتازوا الاختبار واثنان رسبا. هل اجتاز كل الموظفين؟ هل نحتاج عينة أكبر لحسم هذا السؤال؟', 'criterion': 'No, two known counterexamples; population is complete so no larger sample is needed to refute the universal.'},
    {'id': 'LOGIC-V03', 'prompt': 'كل القطط ثدييات. م ثديي. هل يلزم أنه قط؟ أجب بإيجاز.', 'criterion': 'No; mammal does not imply cat.'},
    {'id': 'LOGIC-V04', 'prompt': 'All cats are mammals. Z is not a mammal. Can Z be a cat under these premises? Give a short reason.', 'criterion': 'No, by contraposition; no unsupported converse.'},
    {'id': 'LOGIC-V05', 'prompt': 'رتب D وA وF مرة واحدة لكل عنصر، D قبل A وA قبل F. تحقق من الترتيب بإيجاز.', 'criterion': 'D,A,F exactly once, both precedence constraints satisfied.'},
    {'id': 'LOGIC-V06', 'prompt': 'في الأعداد الحقيقية، هل يوجد x يحقق x <= 2 وx >= 7 معًا؟', 'criterion': 'No solution, incompatible intervals.'},
]


async def evaluate(output: Path, model: str, suite: str):
    if output.exists():
        raise ValueError('Refusing to replace an existing evaluation report')
    output.parent.mkdir(parents=True, exist_ok=True)
    config = {'mode': 'local', 'context_size': 8192, 'max_tokens': 400,
              'temperature': 0, 'seed': 42, 'local_fallback_models': []}
    report = {'schema_version': 'riwaq-logic-probe-1', 'generated_at': datetime.now(timezone.utc).isoformat(),
              'environment': {'platform': platform.platform(), 'python': platform.python_version()},
              'model': model, 'settings': config, 'policy_version': reasoning.POLICY_VERSION,
              'policy_sha256': hashlib.sha256(reasoning.POLICY.encode()).hexdigest(),
              'source_hashes': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                                ('backend/reasoning.py', 'backend/providers.py', 'scripts/evaluate_ai_core_logic.py')},
              'attempts_per_case': 1, 'suite': suite,
              'evaluation_kind': 'live_local_provider_semantic_review_pending',
              'cases': []}
    report['runtime'] = await providers.runtime_status(model, config, probe=False)
    if report['runtime']['status'] == 'unavailable':
        report['status'] = 'blocked_provider_unavailable'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    for case in CASES if suite == 'user' else VARIED_CASES:
        row = {**case, 'answer': '', 'metrics': {}, 'finish_reason': None,
               'semantic_result': 'not_reviewed', 'status': 'running'}
        started = time.monotonic()
        try:
            async for packet in providers.stream('ollama', model,
                [{'role': 'system', 'content': reasoning.POLICY}, {'role': 'user', 'content': case['prompt']}],
                {**config, '_runtime_scope': 'ai-core-logic-synthetic', '_deadline': started+90}):
                row['answer'] += packet.get('content', '')
                row['metrics'].update(packet.get('metrics', {}))
                if packet.get('finish_reason'):
                    row['finish_reason'] = packet['finish_reason']
            row['status'] = 'completed' if row['finish_reason'] == 'stop' else 'incomplete'
        except Exception as exc:
            # No exception body: a provider may include incidental private context.
            row['status'] = 'failed'
            row['error_type'] = type(exc).__name__
        row['seconds'] = round(time.monotonic()-started, 3)
        report['cases'].append(row)
        report['status'] = 'recorded_for_semantic_review'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(case['id'], row['status'], row['seconds'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model', default='qwen3:4b-instruct')
    parser.add_argument('--suite', choices=['user', 'varied'], default='user')
    args = parser.parse_args()
    asyncio.run(evaluate(args.output, args.model, args.suite))
