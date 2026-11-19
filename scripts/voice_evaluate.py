"""Offline exact-text ASR pair evaluation with explicit split and rights audits."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

VERSION = 'voice-evaluate-v1'
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_ROWS = 1000
MAX_TEXT = 4096
MAX_PAIR_CELLS = 4_000_000
MAX_TOTAL_CELLS = 32_000_000
FIELDS = {'id', 'reference', 'hypothesis', 'language', 'dialect', 'condition', 'source_id',
          'speaker_id', 'recording_id', 'split', 'license_verification', 'recording_sha256',
          'reference_sha256', 'hypothesis_sha256', 'sample_kind', 'provenance'}
REQUIRED = FIELDS - {'recording_sha256', 'reference_sha256', 'hypothesis_sha256', 'provenance'}


class EvaluationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError('duplicate_json_key', 'Repeated JSON object key')
        result[key] = value
    return result


def reject_constant(_):
    raise EvaluationError('nonfinite_json', 'Non-finite JSON number is not permitted')


def string(value, field, maximum=200, nullable=False, empty=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > maximum or (not value and not empty):
        raise EvaluationError('invalid_field', f'Invalid {field}')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        raise EvaluationError('invalid_unicode', f'Invalid UTF-8 scalar in {field}') from None
    if any(ord(c) < 32 and c not in '\r\n\t' for c in value):
        raise EvaluationError('invalid_field', f'Control character in {field}')
    if field not in ('reference', 'hypothesis') and (value != value.strip() or any(c in value for c in '\r\n\t')):
        raise EvaluationError('invalid_field', f'Untrimmed or multiline {field}')


def validate(row):
    if not isinstance(row, dict) or set(row) - FIELDS or not REQUIRED <= set(row):
        raise EvaluationError('row_schema', 'Row must contain exactly the documented required and optional fields')
    for field in ('id', 'language', 'condition'):
        string(row[field], field)
    if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', row['language']):
        raise EvaluationError('language_tag', 'language requires a BCP47-style tag, not a semantic coverage claim')
    for field in ('dialect', 'source_id', 'speaker_id', 'recording_id'):
        string(row[field], field, nullable=True)
    for field in ('reference', 'hypothesis'):
        string(row[field], field, MAX_TEXT, empty=True)
    if row['split'] not in ('train', 'dev', 'test'):
        raise EvaluationError('split', 'split must be train, dev, or test')
    if row['sample_kind'] not in ('synthetic_audio', 'human_audio', 'unit_fixture'):
        raise EvaluationError('sample_kind', 'Declare synthetic_audio, human_audio, or unit_fixture')
    rights = row['license_verification']
    if not isinstance(rights, dict) or set(rights) != {'status', 'scope', 'evidence', 'reviewed_by', 'reviewed_at'}:
        raise EvaluationError('license_schema', 'license_verification requires status, scope, evidence, reviewed_by, reviewed_at')
    if rights['status'] != 'verified' or rights['scope'] != 'transcript_evaluation_only':
        raise EvaluationError('license_unverified', 'Verified transcript-evaluation authority is required; training/audio redistribution is not implied')
    for field in ('evidence', 'reviewed_by', 'reviewed_at'):
        string(rights[field], 'license_verification.' + field, 2000 if field == 'evidence' else 200)
    try:
        date.fromisoformat(rights['reviewed_at'])
    except (TypeError, ValueError):
        raise EvaluationError('license_date', 'reviewed_at must be an ISO calendar date') from None
    for field in ('recording_sha256', 'reference_sha256', 'hypothesis_sha256'):
        value = row.get(field)
        if value is not None and (not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)):
            raise EvaluationError('hash_schema', f'{field} must be lowercase SHA256 or null')
    for field in ('reference', 'hypothesis'):
        if row.get(field + '_sha256') not in (None, sha(row[field].encode('utf-8'))):
            raise EvaluationError('text_hash_mismatch', f'{field} SHA256 does not match exact UTF-8 text')
    if 'provenance' in row:
        if not isinstance(row['provenance'], dict) or len(canonical(row['provenance'])) > 8192:
            raise EvaluationError('provenance', 'provenance must be a bounded JSON object; it is not executed or fetched')
    return row


def load_jsonl(paths):
    rows, manifests = [], []
    for path in paths:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise EvaluationError('input_size', 'Input must be a regular JSONL file no larger than 8MiB')
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            total = 0
            line_number = 0
            while True:
                raw = handle.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                line_number += 1
                total += len(raw)
                if len(raw) > MAX_LINE_BYTES or total > MAX_FILE_BYTES:
                    raise EvaluationError('input_size', 'JSONL line/file limit exceeded')
                digest.update(raw)
                if not raw.strip():
                    continue
                if len(rows) >= MAX_ROWS:
                    raise EvaluationError('row_limit', 'At most 1000 pairs per invocation')
                try:
                    row = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
                    rows.append(validate(row))
                except (UnicodeError, json.JSONDecodeError):
                    raise EvaluationError('invalid_json', f'Invalid UTF-8 JSON at input line {line_number}') from None
                except RecursionError:
                    raise EvaluationError('json_depth', 'JSON nesting is excessive') from None
        manifests.append({'filename': path.name, 'bytes': total, 'sha256': digest.hexdigest()})
    if not rows:
        raise EvaluationError('empty_input', 'No pairs to evaluate')
    return rows, manifests


def audit(rows):
    """IDs are declared pseudonyms. Audio hashes are metadata, not freshly read audio."""
    seen_ids, seen_records, seen_audio, seen_rows = set(), set(), set(), set()
    groups = {field: {} for field in ('source_id', 'speaker_id', 'recording_id', 'reference_sha256')}
    missing = defaultdict(int)
    total_cells = 0
    for row in rows:
        validate(row)
        if row['id'] in seen_ids:
            raise EvaluationError('duplicate_id', 'Repeated pair id')
        seen_ids.add(row['id'])
        material = {k: row.get(k) for k in FIELDS - {'id', 'license_verification', 'provenance', 'reference_sha256', 'hypothesis_sha256'}}
        fingerprint = sha(canonical(material))
        if fingerprint in seen_rows:
            raise EvaluationError('duplicate_row', 'Repeated sample content SHA256')
        seen_rows.add(fingerprint)
        for key, seen in (('recording_id', seen_records), ('recording_sha256', seen_audio)):
            value = row.get(key)
            if value is None:
                missing[key] += 1
            elif value in seen:
                raise EvaluationError('duplicate_recording', f'Repeated {key}; do not count a recording twice')
            else:
                seen.add(value)
        for field, values in groups.items():
            value = (sha(row['reference'].encode('utf-8')) if row['reference'] else None) if field == 'reference_sha256' else row.get(field)
            if value is None:
                if field != 'reference_sha256':
                    missing[field] += int(field != 'recording_id')
                continue
            if value in values and values[value] != row['split']:
                raise EvaluationError('split_leakage', f'{field} overlaps train/dev/test')
            values[value] = row['split']
        cells = len(row['reference']) * len(row['hypothesis']) + len(row['reference'].split()) * len(row['hypothesis'].split())
        if cells > MAX_PAIR_CELLS:
            raise EvaluationError('pair_cost', 'Pair exceeds bounded edit-distance work')
        total_cells += cells
        if total_cells > MAX_TOTAL_CELLS:
            raise EvaluationError('total_cost', 'Input exceeds bounded total edit-distance work')
    return {'split_values': sorted({r['split'] for r in rows}),
            'known_overlap_detected': False, 'duplicate_detection': 'exact sample SHA256, recording id, supplied recording SHA256',
            'reference_overlap_policy': 'Identical nonempty reference UTF-8 SHA256 may not cross splits, even if recordings differ.',
            'missing_metadata_counts': dict(missing),
            'complete_identity_metadata': not any(missing.get(x, 0) for x in ('source_id', 'speaker_id', 'recording_id', 'recording_sha256')),
            'claims': 'No overlap found in supplied exact identifiers/hashes. Unknown identities, disguised duplicates, paraphrases, and shared template families are not proved disjoint.',
            'audio_hash_verification': 'declared_only; no audio paths are opened',
            'license_verification': 'required documented local assertion, not automated legal verification',
            'edit_distance_cell_budget_used': total_cells}


def edit_counts(reference, hypothesis):
    """Levenshtein code-point/token alignment. Stable ties: substitute, delete, insert."""
    previous = [(j, 0, 0, j) for j in range(len(hypothesis) + 1)]
    for i, ref in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, hyp in enumerate(hypothesis, 1):
            if ref == hyp:
                current.append(previous[j - 1])
                continue
            diag, deleted, inserted = previous[j - 1], previous[j], current[j - 1]
            candidates = [(diag[0] + 1, diag[1] + 1, diag[2], diag[3]),
                          (deleted[0] + 1, deleted[1], deleted[2] + 1, deleted[3]),
                          (inserted[0] + 1, inserted[1], inserted[2], inserted[3] + 1)]
            current.append(min(candidates, key=lambda x: x[0]))
        previous = current
    distance, substitutions, deletions, insertions = previous[-1]
    return {'errors': distance, 'substitutions': substitutions, 'deletions': deletions,
            'insertions': insertions, 'reference_units': len(reference),
            'hypothesis_units': len(hypothesis), 'rate': distance / len(reference) if reference else None}


def evaluate(rows, inputs=None):
    if len(rows) > MAX_ROWS or not rows:
        raise EvaluationError('row_limit', 'Provide 1 to 1000 pairs')
    split_audit = audit(rows)
    groups, results = {}, []
    for row in rows:
        language = row['language'].lower()
        key = (row['split'], language, row['dialect'], row['condition'], row['sample_kind'])
        if key not in groups:
            groups[key] = {'split': key[0], 'language': key[1], 'dialect': key[2], 'condition': key[3],
                           'sample_kind': key[4], 'pairs': 0, 'exact_text_matches': 0, 'empty_references': 0,
                           'cer': defaultdict(int), 'wer_whitespace': defaultdict(int)}
        group = groups[key]
        cer = edit_counts(row['reference'], row['hypothesis'])
        wer = edit_counts(row['reference'].split(), row['hypothesis'].split())
        group['pairs'] += 1
        group['exact_text_matches'] += int(row['reference'] == row['hypothesis'])
        group['empty_references'] += int(not row['reference'])
        for name, metric in (('cer', cer), ('wer_whitespace', wer)):
            for field, value in metric.items():
                if field != 'rate':
                    group[name][field] += value
        results.append({'id': row['id'], 'split': row['split'], 'language': language, 'dialect': row['dialect'],
                        'condition': row['condition'], 'sample_kind': row['sample_kind'],
                        'reference_sha256': sha(row['reference'].encode('utf-8')),
                        'hypothesis_sha256': sha(row['hypothesis'].encode('utf-8')), 'cer': cer, 'wer_whitespace': wer})
    for group in groups.values():
        for name in ('cer', 'wer_whitespace'):
            metric = dict(group[name])
            metric['rate'] = metric['errors'] / metric['reference_units'] if metric['reference_units'] else None
            group[name] = metric
        group['wer_caution'] = ('Whitespace does not provide language-appropriate word segmentation here; use CER and a separately versioned tokenizer evaluation.'
                                if group['language'].split('-')[0] in ('zh', 'ja', 'th', 'lo', 'km', 'my') else
                                'Whitespace tokens are not a linguistically validated tokenizer; case, punctuation, digits, and diacritics remain significant.')
    return {'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(), 'inputs': inputs or [],
            'method': {'cer': 'Raw Unicode code-point Levenshtein / reference code points; includes exact whitespace, case, punctuation, diacritics, hamza, and digit forms. No Unicode normalization.',
                       'wer_whitespace': 'Levenshtein over Python str.split() Unicode whitespace tokens; no other normalization.',
                       'empty_reference': 'rate=null when denominator=0; insertions remain explicit and contribute to a nonempty same-stratum denominator.',
                       'aggregation': 'Sum errors / sum reference units within exact split/language/dialect/condition/sample_kind only; no cross-language or cross-split score.',
                       'text_storage': 'Report contains hashes and metrics, not reference/hypothesis text.'},
            'audit': split_audit, 'strata': list(groups.values()), 'pairs': results,
            'limitations': ['No model, microphone, network, audio decoder, training, or download is invoked.',
                            'CER/WER are text differences, not semantic accuracy; negation and number errors require separate review.',
                            'Synthetic voices and unit fixtures are never evidence of human, dialect, or noisy real-world accuracy.',
                            'No confidence intervals, success threshold, independent listening review, or general accuracy claim is inferred.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+', help='UTF-8 JSONL; include every split in one invocation for an overlap audit')
    parser.add_argument('--report', type=Path, help='New report path; omitted prints JSON to stdout; existing paths are not overwritten')
    args = parser.parse_args(argv)
    try:
        if args.report and (args.report.exists() or any(args.report.resolve() == p.resolve() for p in args.inputs)):
            raise EvaluationError('report_exists', 'Report path already exists or aliases an input; choose a new path')
        rows, inputs = load_jsonl(args.inputs)
        report = evaluate(rows, inputs)
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
        else:
            print(text, end='')
        return 0
    except (EvaluationError, OSError) as exc:
        print(json.dumps({'success': False, 'code': getattr(exc, 'code', 'io_error'), 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
