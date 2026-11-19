"""Run the frozen synthetic routing evaluation without any network or production data."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('routing_evaluation',ROOT/'tests/test_skill_routing_refinement.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
report={'protocol':module.CASES['protocol'],'fixture_sha256':hashlib.sha256(module.FIXTURES.read_bytes()).hexdigest(),
        'source_sha256':hashlib.sha256((ROOT/'backend/skill_core.py').read_bytes()).hexdigest(),
        'environment':{'python':sys.version.split()[0],'platform':sys.platform,'model_calls':0,'network_requests':0},'splits':{}}
for split in ['development','held_out']:
    cases=[];elapsed=[]
    for case in module.CASES[split]:
        start=time.perf_counter()
        try: errors=module.evaluate_case(case)
        except Exception as exc: errors=[{'exception':type(exc).__name__,'detail':str(exc)}]
        ms=(time.perf_counter()-start)*1000;elapsed.append(ms)
        cases.append({'id':case['id'],'language':case['language'],'passed':not errors,'errors':errors,'milliseconds':round(ms,3)})
    elapsed.sort()
    report['splits'][split]={'total':len(cases),'passed':sum(x['passed'] for x in cases),'cases':cases,
                            'milliseconds_p50':round(elapsed[len(elapsed)//2],3),'milliseconds_p95':round(elapsed[math.ceil(len(elapsed)*.95)-1],3)}
target=Path(sys.argv[1]);target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:{'total':v['total'],'passed':v['passed']} for k,v in report['splits'].items()}))
