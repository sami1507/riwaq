"""Reloaded local inference; reference answers are not accepted by this interface."""
import argparse,json,sys
from pathlib import Path
from runtime_common import load_runtime,generate,sha,adapter_parameter_hash

def main():
 p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--adapter');p.add_argument('--device',choices=['cpu','cuda'],required=True);p.add_argument('--max-requests',type=int,default=100);a=p.parse_args()
 if not 1<=a.max_requests<=200:raise ValueError('Request cap1..200')
 sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
 model,tok,base_hash=load_runtime(a.base,a.device,adapter=a.adapter);model.eval();verified=False
 if a.adapter:
  evidence=json.loads((Path(a.adapter)/'runtime_manifest.json').read_text(encoding='utf-8'))
  if adapter_parameter_hash(model)!=evidence['trainable_parameter_sha256']:raise ValueError('Reloaded adapter parameters differ')
  verified=True
 print(json.dumps({'stage':'ready','base_report_sha256':base_hash,'adapter':a.adapter,'adapter_reload_verified':verified}),file=sys.stderr,flush=True)
 count=0
 for line in sys.stdin:
  if not line.strip():continue
  count+=1
  if count>a.max_requests or len(line.encode())>64000:raise ValueError('Request cap exceeded')
  row=json.loads(line)
  if not isinstance(row,dict) or set(row)-{'id','messages','max_new_tokens'} or 'messages' not in row:raise ValueError('Only id/messages/max_new_tokens accepted')
  messages=row['messages']
  if not isinstance(messages,list) or not 1<=len(messages)<=12:raise ValueError('Invalid messages')
  for message in messages:
   if set(message)!={'role','content'} or message['role'] not in {'system','user','assistant'} or not isinstance(message['content'],str):raise ValueError('Invalid envelope')
  if messages[-1]['role']!='user':raise ValueError('Inference must end with user, never gold answer')
  print(json.dumps({'id':row.get('id'),'adapter_reload_verified':verified,**generate(model,tok,messages,row.get('max_new_tokens',256))},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=='__main__':main()
