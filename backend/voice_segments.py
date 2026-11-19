"""Stable TTS parts from a completed text, never provisional model deltas."""
import re
import hashlib
import io
import math
import wave

MAX_PARTS=6


def confirm_audio(raw,receipt):
    if not isinstance(raw,bytes) or not 0<len(raw)<=8*1024**2:raise ValueError('Speech part byte budget')
    expected=receipt.get('audio',{})
    if expected.get('sha256')!=hashlib.sha256(raw).hexdigest():raise ValueError('Speech part receipt mismatch')
    with wave.open(io.BytesIO(raw),'rb') as audio:
        frames=audio.getnframes();rate=audio.getframerate();channels=audio.getnchannels();width=audio.getsampwidth()
        if audio.getcomptype()!='NONE' or channels not in (1,2) or width not in (1,2,3,4) or not 8000<=rate<=48000:raise ValueError('Unsupported speech part PCM')
        duration=frames/rate
        if not 0<duration<=180 or len(audio.readframes(frames))!=frames*channels*width:raise ValueError('Truncated or excessive speech part')
    reported=expected.get('duration')
    if type(reported) not in (int,float) or not math.isfinite(reported) or abs(reported-duration)>.02:raise ValueError('Speech duration receipt mismatch')
    return duration


def parts(text):
    if not isinstance(text,str) or not text.strip() or len(text)>1800:
        raise ValueError('Completed speech text must contain 1 to 1800 characters')
    sentences=[m.group() for m in re.finditer(r'.+?(?:[.!?؟](?:\s+|$)|$)',text,re.S)]
    grouped=[]
    for sentence in sentences:
        if grouped and len(grouped[-1])+len(sentence)<=360:grouped[-1]+=sentence
        else:grouped.append(sentence)
    if len(grouped)>MAX_PARTS:grouped=grouped[:MAX_PARTS-1]+[''.join(grouped[MAX_PARTS-1:])]
    if ''.join(grouped)!=text:raise ValueError('Speech segmentation must preserve the completed text')
    result=[];start=0
    for index,part in enumerate(grouped):
        result.append({'index':index,'text':part,'start':start,'end':start+len(part)})
        start+=len(part)
    return result
