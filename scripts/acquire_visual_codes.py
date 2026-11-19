"""Explicit small reviewed PyPI acquisition; never executed by request handling."""
from pathlib import Path
import hashlib,json,urllib.request,urllib.parse,zipfile,io

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/closure-20261116/visual-codes/acquisition'
LIMIT=16*1024**2
PREVIOUS=ROOT/'reports/closure-20261116/office-acquisition.json'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    report=json.loads((OUT/'ledger.json').read_text()) if (OUT/'ledger.json').exists() else {'state':'reserved','byte_limit':LIMIT,'actual_bytes':0,'previous_ledger':'reports/closure-20261116/office-acquisition.json',
      'previous_ledger_sha256':hashlib.sha256(PREVIOUS.read_bytes()).hexdigest(),'aggregate_before':9842038189,'aggregate_limit':10737418240,'events':[],'packages':[]}
    if report['previous_ledger_sha256']!=hashlib.sha256(PREVIOUS.read_bytes()).hexdigest() or report['actual_bytes']>LIMIT:raise ValueError('Acquisition ledger changed')
    def save():
        report['aggregate_after']=report['aggregate_before']+report['actual_bytes']
        (OUT/'ledger.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    save()
    def fetch(url,label):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in {'pypi.org','files.pythonhosted.org'}:raise ValueError('Unapproved acquisition destination')
        event={'url':url,'label':label,'received_bytes':0,'success':False};report['events'].append(event);save();chunks=[]
        try:
            with urllib.request.urlopen(url,timeout=30) as response:
                if urllib.parse.urlparse(response.url).hostname!=parsed.hostname:raise ValueError('Unexpected redirect')
                while True:
                    remaining=LIMIT-report['actual_bytes']
                    if remaining<=0:raise ValueError('Reserved byte budget exhausted')
                    chunk=response.read(min(65536,remaining))
                    if not chunk:break
                    report['actual_bytes']+=len(chunk);event['received_bytes']+=len(chunk);chunks.append(chunk);save()
            raw=b''.join(chunks);event['success']=True;event['sha256']=hashlib.sha256(raw).hexdigest();save();return raw
        except Exception as exc:event['error_type']=type(exc).__name__;report['state']='failed';save();raise
    for name in ('segno','python-barcode','zxing-cpp'):
        existing=next((item for item in report['packages'] if item['name']==name),None)
        if existing:
            if hashlib.sha256((OUT/existing['filename']).read_bytes()).hexdigest()!=existing['sha256']:raise ValueError('Cached wheel changed')
            continue
        metadata_path=OUT/(name+'-metadata.json')
        raw=metadata_path.read_bytes() if metadata_path.exists() else fetch('https://pypi.org/pypi/'+name+'/json',name+' metadata');metadata_path.write_bytes(raw)
        meta=json.loads(raw);version=meta['info']['version'];candidates=[]
        for item in meta['urls']:
            filename=item['filename']
            if filename.endswith('py3-none-any.whl') or (name=='zxing-cpp' and 'cp312-abi3-manylinux' in filename and filename.endswith('x86_64.whl')):candidates.append(item)
        if len(candidates)!=1:raise ValueError('Ambiguous/missing compatible pinned wheel for '+name)
        item=candidates[0]
        if item['size']>LIMIT-report['actual_bytes']:raise ValueError('Wheel exceeds remaining byte budget')
        data=fetch(item['url'],item['filename'])
        if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['digests']['sha256']:raise ValueError('Artifact integrity mismatch')
        (OUT/item['filename']).write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            metadata=[x for x in archive.namelist() if x.endswith('.dist-info/METADATA')]
            if len(metadata)!=1:raise ValueError('Missing wheel metadata')
            (OUT/(name+'-WHEEL-METADATA.txt')).write_bytes(archive.read(metadata[0]))
        report['packages'].append({'name':name,'version':version,'filename':item['filename'],'bytes':len(data),'sha256':item['digests']['sha256'],
          'license':meta['info'].get('license_expression') or meta['info'].get('license'),'project_urls':meta['info'].get('project_urls'),'requires_dist':meta['info'].get('requires_dist')});save()
    report['state']='complete';save();print(json.dumps({'state':report['state'],'bytes':report['actual_bytes'],'packages':report['packages']}))


if __name__=='__main__':main()
