"""Legacy structural preflight; uploaded permission claims are not authorization.

No model downloads, training or user conversation collection occur here.
For trusted rights/consent/review gates use training.learning_data. This legacy
format cannot verify the provenance of its own permission fields.
Usage: python training/validate_data.py train.jsonl validation.jsonl test.jsonl
"""
import hashlib
import json
import sys
from pathlib import Path

def validate(paths):
    seen_ids = set()
    seen_text = set()
    sources = {}
    report = {}
    for split, path in zip(('train', 'validation', 'test'), paths):
        count = 0
        for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip(): continue
            item = json.loads(line)
            prefix = f'{split}:{number}'
            if not item.get('id') or item['id'] in seen_ids:
                raise ValueError(f'{prefix}: missing or duplicate id')
            if item.get('training_permission') is not True or not item.get('permission_reference'):
                raise ValueError(f'{prefix}: explicit permission and evidence required')
            if not item.get('source_group') or not item.get('license'):
                raise ValueError(f'{prefix}: source_group and license required')
            if item.get('redaction_reviewed') is not True:
                raise ValueError(f'{prefix}: personal/secret data review required')
            messages = item.get('messages')
            if not isinstance(messages, list) or len(messages) < 2:
                raise ValueError(f'{prefix}: conversation requires at least two messages')
            if messages[-1].get('role') != 'assistant':
                raise ValueError(f'{prefix}: final message must be an approved assistant target')
            for message in messages:
                if message.get('role') not in ('system','user','assistant') or not isinstance(message.get('content'), str) or not message['content'].strip():
                    raise ValueError(f'{prefix}: invalid message')
            fingerprint = hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if fingerprint in seen_text:
                raise ValueError(f'{prefix}: duplicate conversation; prevent train/eval leakage')
            group = item['source_group']
            if group in sources and sources[group] != split:
                raise ValueError(f'{prefix}: source group spans splits')
            sources[group] = split
            seen_ids.add(item['id'])
            seen_text.add(fingerprint)
            count += 1
        if not count: raise ValueError(f'{split}: empty split')
        report[split] = count
    return {'status':'structural_only','counts':report,'training_started':False,
            'training_authorized':False,'requires_trusted_review':True,
            'limit':'Uploaded permission/review claims were checked for shape only; use trusted server review receipts.'}

if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: validate_data.py train.jsonl validation.jsonl test.jsonl')
    try:
        print(json.dumps(validate(sys.argv[1:]),ensure_ascii=False,indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(str(error))
