"""Fetch pinned official configuration/tokenizer/license only, charged to shared budget."""
import json,hashlib
from pathlib import Path
from runtime_setup import fetch,sha,RUNTIME
REPO='Qwen/Qwen3-4B-Instruct-2507'
def main():
 directory=RUNTIME/'assets';directory.mkdir(parents=True,exist_ok=True);manifest=directory/'assets.json'
 if manifest.exists():
  existing=json.loads(manifest.read_text())
  for name,record in existing['files'].items():assert sha(directory/name)==record['sha256']
  print(json.dumps({'revision':existing['revision'],'verified':True}));return
 metadata=json.loads(fetch('https://huggingface.co/api/models/'+REPO,max_bytes=5*1024**2));revision=metadata['sha']
 assert len(revision)==40
 files=['config.json','generation_config.json','tokenizer_config.json','tokenizer.json','vocab.json','merges.txt','LICENSE'];records={}
 for name in files:
  destination=directory/name
  if destination.exists():raise ValueError('Unmanifested asset exists; inspect rather than overwrite')
  body=fetch(f'https://huggingface.co/{REPO}/resolve/{revision}/{name}',max_bytes=25*1024**2)
  destination.write_bytes(body);records[name]={'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
 manifest.write_text(json.dumps({'repository':REPO,'revision':revision,'files':records,'weights_downloaded':False},indent=2),encoding='utf-8')
 print(json.dumps({'revision':revision,'bytes':sum(x['bytes'] for x in records.values())}))
if __name__=='__main__':main()
