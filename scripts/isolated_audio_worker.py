"""Fixed Docker audio decoder: stdin bytes to bounded flat PCM, no host path."""
import sys
sys.path.insert(0,'/opt/riwaq')
sys.path.insert(0,'/opt/audio-packages')
import base64,hashlib
from backend import isolated_audio as protocol

def main():
    raw=sys.stdin.buffer.read(protocol.MAX_PAYLOAD+1);source_hash=None
    try:
        if len(raw)>protocol.MAX_PAYLOAD:raise ValueError('payload_limit')
        value=protocol.loads(raw)
        if not isinstance(value,dict) or set(value)!={'version','body'} or value['version']!=protocol.VERSION:raise ValueError('payload_fields')
        if not isinstance(value['body'],str) or len(value['body'])>4*((protocol.MAX_INPUT+2)//3):raise ValueError('body_limit')
        body=base64.b64decode(value['body'],validate=True);source_hash=hashlib.sha256(body).hexdigest()
        from backend.audio_decode import decode,AudioRejected
        try:
            pcm,source,engine=decode(body)
            result={'version':protocol.VERSION,'input_sha256':source_hash,'success':True,'source':source,'engine':engine,
                'pcm':{'format':'f32le','sample_rate':protocol.RATE,'channels':1,'samples':len(pcm)//4,'bytes':len(pcm),
                       'sha256':hashlib.sha256(pcm).hexdigest(),'base64':base64.b64encode(pcm).decode('ascii')}}
        except AudioRejected as exc:result={'version':protocol.VERSION,'input_sha256':source_hash,'success':False,'error_code':exc.code}
        output=protocol.dump(result)
        if len(output)>protocol.MAX_ENVELOPE:raise ValueError('output_limit')
    except Exception:
        output=protocol.dump({'version':protocol.VERSION,'input_sha256':source_hash,'success':False,'error_code':'audio_invalid'})
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush();return 0

if __name__=='__main__':raise SystemExit(main())
