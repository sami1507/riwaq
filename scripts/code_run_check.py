"""Live check (python scripts/code_run_check.py --out DIR): does the isolated worker really run chat snippets and report their output?"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import code_run  # noqa: E402

CASES = [('x = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))', 'ok', '4\n'), ('print("ab" * 2 + "c")', 'ok', 'ababc\n'),
         ('def f(n):\n    return n if n < 2 else f(n - 1) + f(n - 2)\nprint(f(7))', 'ok', '13\n'), ('print([i * i for i in range(4) if i % 2])', 'ok', '[1, 9]\n'),
         ('print("قبل")\nprint(1 / 0)', 'error: ZeroDivisionError: division by zero', 'قبل\n'), ('while True:\n    pass', 'stopped: exceeded 15 seconds', ''),
         # Isolation: no network, and the host's files are not there.
         ('import socket\ntry:\n    socket.create_connection(("1.1.1.1", 53), timeout=3)\n    print("NETWORK")\nexcept OSError:\n    print("no network")', 'ok', 'no network\n'),
         ('import os\nprint(os.path.exists("/mnt/d") or os.path.exists("C:/Users") or os.path.exists("/host"))', 'ok', 'False\n')]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', required=True); args = parser.parse_args()
    rows = []
    for code, status, stdout in CASES:
        result = code_run.run(code)
        ok = result.get('ran') is True and result.get('status') == status and result.get('stdout') == stdout
        rows.append({'code_first_line': code.splitlines()[0], 'expected': [status, stdout], 'result': result, 'ok': ok})
        print(code.splitlines()[0], '=>', 'OK' if ok else result)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'code_run_check.json').write_text(json.dumps({'passed': sum(r['ok'] for r in rows), 'total': len(rows), 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'passed': sum(r['ok'] for r in rows), 'total': len(rows)}))
    return 0 if all(r['ok'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
