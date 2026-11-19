"""Read-only names/status audit; never prints environment values."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.environment_audit import audit_environment

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output');args=p.parse_args()
    result=audit_environment(ROOT);encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        target=Path(args.output)
        if target.exists():p.exit(2,'Choose a new audit output file.\n')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(encoded,encoding='utf-8')
        print(json.dumps({'state':result['state'],'variables':len(result['variables']),'client_secret_references':len(result['client_secret_references']),'values_included':False}))
    else:print(encoded)
    return 1 if result['state']=='blocked' else 0
if __name__=='__main__':raise SystemExit(main())
