"""Postprocess frozen outputs; never changes grades or sends model requests."""
import hashlib,json,statistics,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
reports={p:json.loads((ROOT/f'reports/reasoning-{p}.json').read_text(encoding='utf-8')) for p in ['dev','heldout','repeat']}
rows=[r for report in reports.values() for r in report['attempts']]
held={v:{r['task_id']:r for r in reports['heldout']['attempts'] if r['variant']==v and not r['grade']['review_required']} for v in ['baseline','tools']}
transitions={'both_correct':[],'improved':[],'regressed':[],'both_wrong':[]}
for id,b in held['baseline'].items():
 a=b['grade']['structured_pass'];z=held['tools'][id]['grade']['structured_pass'];transitions['both_correct' if a and z else 'improved' if z else 'regressed' if a else 'both_wrong'].append(id)
groups=defaultdict(list)
for r in rows:
 if r['split']=='dev' and r['task_id'] in {'M-D01','C-D01','R-D01'}:groups[(r['task_id'],r['variant'],r['model'])].append(r)
stability=[]
for (id,v,m),items in sorted(groups.items()):
 items.sort(key=lambda r:r['repeat']);stability.append({'task':id,'variant':v,'model':m,'repeats':len(items),'correct':[r['grade']['structured_pass'] for r in items],'unique_outputs':len({r['final_output'] for r in items}),'seconds':[r['seconds'] for r in items]})
identities={phase:{m['name']:m['digest'] for m in report['models']['models']} for phase,report in reports.items()}
assert identities['dev']==identities['heldout']==identities['repeat']
assert all(r['restored_equal'] and r['completion_status']=='complete' for r in rows)
assert all(not report['network_guard']['external_blocked'] for report in reports.values())
for r in rows:
 run=r['run_receipt'];assert run['model_calls']<=run['ceilings']['model_calls'] and run['tool_calls']<=run['ceilings']['tool_calls'] and run['reserved_tokens']<=run['ceilings']['reserved_tokens']
reviewed=[]
for r in rows:
 for c in r['run_receipt']['checks']:
  if c['kind']=='calculate' and c['status']=='pass':
   value=json.loads(c['details']);reviewed.append({'attempt':r['attempt_id'],'expression':value['expression'],'exact':value['exact'],'decimal':value['decimal'],'rounded':value['rounded_2']})
result={'date':'2026-11-13','generations':len(rows),'phase_counts':{p:len(r['attempts']) for p,r in reports.items()},'comparisons':{p:r['summary'] for p,r in reports.items() if p!='repeat'},'paired_heldout':transitions,'repeated_sample':stability,'models':identities['dev'],'ollama_version':reports['dev']['ollama_version'],'all_restored_equal':True,'all_completed':True,'budgets_respected':True,'total_seconds':sum(r['seconds'] for r in rows),'model_calls':sum(r['run_receipt']['model_calls'] for r in rows),'tool_calls':sum(r['run_receipt']['tool_calls'] for r in rows),'input_tokens':sum(r['run_receipt']['actual_input_tokens'] for r in rows),'output_tokens':sum(r['run_receipt']['actual_output_tokens'] for r in rows),'paid_provider_usd':0,'electricity_hardware_cost':'not estimated','network_guard':{p:r['network_guard'] for p,r in reports.items()},'review_revisions_attempted':sum(any(c['kind']=='revision' and c['status']!='not_run' for c in r['run_receipt']['checks']) for r in rows),'arithmetic_receipts':reviewed,'source_reports':{p:hashlib.sha256((ROOT/f'reports/reasoning-{p}.json').read_bytes()).hexdigest() for p in reports},'adoption':'baseline default; tools explicit experimental opt-in; development gate failed','limits':['Strict structured-task score is not factual accuracy alone.','ASGI buffering: no first-token timing.','Fixed seed; tiny repeated sample.','No independent human review.','Network guard is in process; device/Ollama network was not disconnected.','Raw repeat summary is not a repeat statistic; use repeated_sample grouped by model here.']}
(ROOT/'reports/reasoning-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:result[k] for k in ['generations','total_seconds','model_calls','tool_calls','input_tokens','output_tokens','ollama_version','paired_heldout','review_revisions_attempted']},ensure_ascii=True))
