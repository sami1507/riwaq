"""Create only the fixed synthetic fixture using Riwaq's existing writer."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.skill_artifacts import build_artifact

def create(output):
    output = Path(output).resolve()
    output.relative_to((ROOT / 'reports').resolve())
    if output.exists():
        raise ValueError('Fresh fixture directory required')
    spec = json.loads((ROOT / 'tests/fixtures/ai_core_visual/word-reference.json').read_text(encoding='utf-8'))
    artifact = build_artifact('docx', spec['payload'])
    output.mkdir(parents=True)
    path = output / 'arabic-table.docx'
    path.write_bytes(artifact.content)
    report = {'source': 'Riwaq backend.skill_artifacts.build_artifact', 'synthetic': True,
              'python': sys.version, 'file': str(path.relative_to(ROOT)),
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'verification': artifact.verification, 'native_rendered': False,
              'reference': 'tests/fixtures/ai_core_visual/word-reference.json'}
    (output / 'word-creation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report

if __name__ == '__main__':
    print(json.dumps(create(sys.argv[1]), ensure_ascii=False))
