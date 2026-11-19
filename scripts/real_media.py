"""Live image + Arabic STT smoke, using a synthetic image and public voice sample."""
import io
import json
import re
import sys
import time
import wave
from pathlib import Path
import av
import httpx
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding='utf-8')
root=Path(__file__).resolve().parent.parent
client=httpx.Client(base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'},timeout=200,trust_env=False)
picture=Image.new('RGB',(640,400),'white');draw=ImageDraw.Draw(picture)
draw.rectangle((60,100,240,280),fill='red');draw.ellipse((370,100,550,280),fill='blue')
blob=io.BytesIO();picture.save(blob,format='PNG')
(root/'reports'/'vision-fixture.png').write_bytes(blob.getvalue())
started=time.perf_counter()
response=client.post('/api/media/vision',files={'file':('shapes.png',blob.getvalue(),'image/png')},data={'prompt':'صف الشكلين ولونيهما بالعربية فقط. أي شكل على اليسار وأيهما على اليمين؟'})
response.raise_for_status();vision=response.json();print(json.dumps(vision,ensure_ascii=False),flush=True)
text=vision['text']
vision_ok=bool(re.search('[\u0600-\u06ff]',text)) and ('مربع' in text or 'مستطيل' in text) and ('أحمر' in text or 'الأحمر' in text) and ('دائر' in text) and ('أزرق' in text or 'الأزرق' in text)
report={'vision':{'passed':vision_ok,'model':vision['model'],'digest':vision['digest'],'response':text,'seconds':round(time.perf_counter()-started,2),'scope':'Synthetic red square left / blue circle right, Arabic description. No real-world OCR benchmark.'}}
(root/'reports/media-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
assert vision_ok,text
wav=io.BytesIO()
with av.open('D:/RiwaqRuntime/arabic-validation.mp3') as audio:
    resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
    with wave.open(wav,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
        for frame in audio.decode(audio=0):
            for part in resampler.resample(frame):out.writeframes(part.to_ndarray().tobytes())
        for part in resampler.resample(None):out.writeframes(part.to_ndarray().tobytes())
started=time.perf_counter()
response=client.post('/api/media/transcribe',files={'file':('arabic.wav',wav.getvalue(),'audio/wav')},data={'language':'ar'})
if response.status_code!=200:print(response.text,flush=True)
response.raise_for_status();stt=response.json();print(json.dumps(stt,ensure_ascii=False),flush=True)
stt_ok=len(re.findall('[\u0600-\u06ff]',stt['text']))>20 and stt['language']=='ar'
report['stt']={'passed':stt_ok,'model_path':stt['model_path'],'fingerprint':stt['fingerprint'],'text':stt['text'],'seconds':round(time.perf_counter()-started,2),'duration':stt['duration'],'source':'https://huggingface.co/rhasspy/piper-voices/tree/main/ar/ar_JO/kareem/medium/samples','scope':'Public synthetic Arabic speech smoke. No gold transcript/WER benchmark; not representative of accents or microphone noise.','limits':stt['limits']}
(root/'reports/media-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
assert stt_ok,stt['text']
print('LIVE MEDIA PASSED')
