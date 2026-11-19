"""Bounded local media composition using installed PyAV/FFmpeg libraries.

Public wrappers run fixed workers under visual_runner. No arbitrary FFmpeg CLI,
network input, model calls, source-file modification, or synthetic video claims.
"""
from __future__ import annotations
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
VERSION='video-compose-v1'
SIZES={(1280,720),(720,1280),(640,360),(360,640),(960,540),(540,960)}
RATE=48000
MAX_INPUT=8*1024**2
MAX_OUTPUT=64*1024**2
MEDIA_FORMATS='mov,matroska,webm,wav,mp3'


class VideoError(ValueError):pass


def sha(data):return hashlib.sha256(data).hexdigest()
def packed(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def frames(value,fps):return int((Decimal(str(value))*fps).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def samples(value):return frames(value,RATE)
def number(value,low,high,label):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise VideoError('Invalid '+label)
    return value

def keys(value,allowed,required,label):
    if not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value):raise VideoError('Invalid '+label+' fields')

def ident(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):raise VideoError('Invalid identifier')
    return value


def validate_spec(spec):
    keys(spec,{'width','height','fps','background','clips','texts','audio','brief'},{'width','height','fps','background','clips','texts','audio'},'timeline')
    if 'brief' in spec and (not isinstance(spec['brief'],dict) or any(not isinstance(v,str) or len(v)>2000 for v in spec['brief'].values())):raise VideoError('Invalid production brief')
    if type(spec['width']) is not int or type(spec['height']) is not int or (spec['width'],spec['height']) not in SIZES:raise VideoError('Unsupported output dimensions')
    if type(spec['fps']) is not int or spec['fps'] not in (24,30):raise VideoError('Frame rate must be 24 or 30')
    if not isinstance(spec['background'],str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',spec['background']):raise VideoError('Invalid background')
    for name,maximum in [('clips',12),('texts',24),('audio',4)]:
        if not isinstance(spec[name],list) or len(spec[name])>maximum:raise VideoError('Too many '+name)
    if not spec['clips']:raise VideoError('At least one clip is required')
    ids=set();total=0;raw_total=Decimal('0');fps=spec['fps'];effective=[]
    def unique(item):
        name=ident(item['id'])
        if name in ids:raise VideoError('Duplicate timeline identifier')
        ids.add(name)
    for clip in spec['clips']:
        keys(clip,{'id','asset_id','start','duration','muted','gain','motion'},{'id','asset_id','start','duration','muted','gain','motion'},'clip')
        unique(clip);ident(clip['asset_id']);number(clip['start'],0,120,'clip source start');number(clip['duration'],.05,60,'clip duration');number(clip['gain'],0,2,'clip gain')
        if type(clip['muted']) is not bool or clip['motion'] not in ('none','pan_zoom'):raise VideoError('Invalid clip motion or mute')
        raw_total+=Decimal(str(clip['duration']))
        count=frames(raw_total,fps)-total
        if count<1:raise VideoError('Clip is shorter than one frame')
        effective.append({'id':clip['id'],'timeline_start_frame':total,'frames':count,'requested_duration':clip['duration'],
                          'effective_duration':count/fps,'requested_source_start':clip['start'],'source_start':frames(clip['start'],fps)/fps})
        total+=count
    if total>fps*60:raise VideoError('Timeline exceeds 60 seconds after frame quantization')
    duration=total/fps
    for text in spec['texts']:
        fields={'id','kind','text','start','end','x','y','width','height','language','direction','font','size','weight','color','align','protected'}
        keys(text,fields,fields-{'protected'},'text');unique(text)
        if text.get('protected',True) is not True:raise VideoError('Text must remain protected')
        if text['kind'] not in ('title','subtitle') or not isinstance(text['text'],str) or not 1<=len(text['text'])<=1000:raise VideoError('Invalid text')
        if any(ord(c)<32 and c not in '\t\r\n' or 0xd800<=ord(c)<=0xdfff for c in text['text']):raise VideoError('Unsupported text control or surrogate')
        for k in ('start','end'):number(text[k],0,60,k)
        if frames(text['end'],fps)>total:raise VideoError('Text ends beyond quantized timeline')
        if frames(text['end'],fps)<=frames(text['start'],fps):raise VideoError('Text requires at least one visible frame')
        for k in ('x','y'):number(text[k],0,max(spec['width'],spec['height']),k)
        for k in ('width','height'):number(text[k],1,max(spec['width'],spec['height']),k)
        if text['x']+text['width']>spec['width'] or text['y']+text['height']>spec['height']:raise VideoError('Text box outside output')
        number(text['size'],8,256,'text size')
        if text['weight'] not in (400,600) or type(text['weight']) is not int or text['font'] not in ('arabic','latin','hebrew') or text['language'] not in ('ar','en','he','es') or text['direction'] not in ('rtl','ltr','auto') or text['align'] not in ('left','center','right') or not re.fullmatch(r'#[a-fA-F0-9]{6}',text['color']):raise VideoError('Invalid text style')
    for audio in spec['audio']:
        fields={'id','asset_id','start','source_start','duration','gain','role'}
        keys(audio,fields,fields,'audio');unique(audio);ident(audio['asset_id'])
        number(audio['start'],0,60,'audio timeline start');number(audio['source_start'],0,120,'audio source start');number(audio['duration'],.01,60,'audio duration');number(audio['gain'],0,2,'audio gain')
        if samples(audio['start'])+samples(audio['duration'])>samples(duration) or audio['role'] not in ('narration','music','effect'):raise VideoError('Audio outside timeline after 48 kHz sample rounding or invalid role')
    return {'frames':total,'duration':duration,'clips':effective,'timing':'Clip boundaries, video source trims and text boundaries use nearest output frame, decimal half-up. Imported audio start, source trim and duration use nearest 48 kHz sample, decimal half-up; selections extending beyond effective video duration are rejected, never shortened. No optical interpolation.'}


def local_asset(value,folder):
    if not isinstance(value,str) or '://' in value:raise VideoError('Only materialized local assets are accepted')
    path=Path(value).resolve();folder=Path(folder).resolve()
    if not path.is_relative_to(folder) or not path.is_file():raise VideoError('Asset is outside its job directory')
    if path.stat().st_size<=0 or path.stat().st_size>MAX_INPUT:raise VideoError('Asset exceeds 8 MiB')
    return path


def deny_extra_io(*args,**kwargs):raise VideoError('External media references are disabled')


@contextmanager
def media_open(path):
    import av
    # Custom file IO plus format restriction prevents playlist/network/device inputs.
    # Even nested references inside an allowed demuxer cannot open another resource.
    with Path(path).open('rb') as stream:
        container=av.open(stream,mode='r',options={'format_whitelist':MEDIA_FORMATS,'protocol_whitelist':'',
            'enable_drefs':'0','use_absolute_path':'0','probesize':'1048576','analyzeduration':'5000000','max_probe_packets':'256'},
            io_open=deny_extra_io,timeout=(10,10))
        try:yield container
        finally:container.close()


def magic(path):
    with Path(path).open('rb') as f:head=f.read(32)
    if head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or head[:4]==b'RIFF' and head[8:12]==b'WEBP':return 'image'
    if len(head)>=12 and head[4:8]==b'ftyp' or head.startswith(b'\x1aE\xdf\xa3'):return 'video'
    if head[:4]==b'RIFF' and head[8:12]==b'WAVE' or head[:3]==b'ID3' or len(head)>1 and head[0]==255 and head[1]&0xe0==0xe0:return 'audio'
    raise VideoError('Unsupported media signature: PNG/JPEG/WebP, MP4/WebM or WAV/MP3 only')


def stream_duration(container,stream):
    duration=float(stream.duration*stream.time_base) if stream.duration is not None and stream.time_base else float(container.duration or 0)/1000000
    if not math.isfinite(duration) or not 0<duration<=120:raise VideoError('Missing or unsupported media duration; maximum 120 seconds')
    return duration


def probe_local(path):
    from PIL import Image,ImageOps
    path=Path(path);kind=magic(path);result={'kind':kind,'bytes':path.stat().st_size,'sha256':sha(path.read_bytes()),'has_audio':False,'duration':None,'fps':None,'codec':None,'width':None,'height':None}
    if kind=='image':
        with Image.open(path) as im:
            if getattr(im,'n_frames',1)!=1 or im.width*im.height>4_000_000:raise VideoError('Animated or oversized image is unsupported')
            im=ImageOps.exif_transpose(im);im.load();result.update(width=im.width,height=im.height,codec=im.format or 'raster',rotation_applied='EXIF transpose')
        return result
    with media_open(path) as container:
        if len(container.streams)>8:raise VideoError('Too many media streams')
        result['container']=container.format.name;result['has_audio']=bool(container.streams.audio)
        if container.streams.video:
            if kind=='audio':raise VideoError('Unexpected video in audio upload')
            stream=container.streams.video[0];stream.codec_context.thread_count=2
            if stream.width*stream.height>4_000_000 or not stream.width or not stream.height:raise VideoError('Video resolution exceeds 4 megapixels')
            rate=float(stream.average_rate or stream.base_rate or 0)
            if not 0<rate<=60:raise VideoError('Unsupported source frame rate; maximum 60 fps')
            if stream.sample_aspect_ratio and Fraction(stream.sample_aspect_ratio)!=1:raise VideoError('Non-square source pixels require explicit normalization outside this pilot')
            first=next(container.decode(video=0),None)
            if first is None:raise VideoError('Video has no decodable first frame')
            rotation=first.rotation
            if rotation not in (-180,-90,0,90,180):raise VideoError('Unsupported non-right-angle source rotation')
            w,h=first.width,first.height
            if abs(rotation)==90:w,h=h,w
            result.update(kind='video',width=w,height=h,duration=stream_duration(container,stream),fps=rate,codec=stream.codec_context.name,rotation=rotation)
        elif container.streams.audio:
            stream=container.streams.audio[0]
            if stream.codec_context.sample_rate>192000 or len(stream.codec_context.layout.channels)>8:raise VideoError('Audio sample rate or channel count exceeds bound')
            result.update(kind='audio',duration=stream_duration(container,stream),codec=stream.codec_context.name,
                          sample_rate=stream.codec_context.sample_rate,channels=len(stream.codec_context.layout.channels))
        else:raise VideoError('No supported media stream')
    return result


def _invoke(mode,payload,folder,stop,progress=lambda _:None):
    if getattr(sys,'frozen',False):raise VideoError('packaged_worker_unavailable: no configured fixed video worker; no fallback was started')
    if not (ROOT/'scripts/video_render.py').is_file():raise VideoError('video_worker_unavailable')
    from backend import visual_runner
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    input_path=folder/('video-'+mode+'-input.json');input_path.write_text(packed(payload),encoding='utf-8')
    receipt=visual_runner.execute([sys.executable,'-B',str(ROOT/'scripts/video_render.py'),'--mode',mode,'--input',str(input_path),'--output',str(folder)],
        ROOT,folder,stop,progress,seconds=30 if mode=='probe' else 300,memory=2*1024**3)
    result_path=folder/('probe.json' if mode=='probe' else 'report.json')
    if not result_path.is_file() or result_path.stat().st_size>2*1024**2:raise VideoError('Video worker returned no bounded report: '+str(receipt.get('abort_reason')))
    result=json.loads(result_path.read_text(encoding='utf-8'))
    result['resources']={k:v for k,v in receipt.items() if k!='log_tail'}
    if receipt.get('abort_reason') or not result.get('success'):raise VideoError(result.get('error') or 'Video worker: '+str(receipt.get('abort_reason')))
    return result['metadata']|{'resources':result['resources']} if mode=='probe' else result


def probe(path,folder,stop):return _invoke('probe',{'path':str(local_asset(str(path),folder))},folder,stop)
def render(payload,folder,stop,progress):return _invoke('render',payload,folder,stop,progress)

class VideoSampler:
    def __init__(self,path,start):
        self.context=media_open(path);self.container=self.context.__enter__()
        try:
            self.stream=self.container.streams.video[0];self.stream.codec_context.thread_count=2
            self.origin=float(self.container.start_time or 0)/1000000
            self.rate=float(self.stream.average_rate or self.stream.base_rate)
            if start>0:self.container.seek(max(0,int((start+self.origin)*1000000)),backward=True,any_frame=False)
            self.iterator=iter(self.container.decode(video=0));self.current=None;self.future=None;self.ended=False;self.count=0;self.previous_time=-math.inf
            self.advance()
        except BaseException:
            self.close();raise
    def advance(self):
        frame=next(self.iterator,None)
        if frame is None:self.ended=True;self.future=None;return
        self.count+=1
        if self.count>20000 or frame.width*frame.height>4_000_000:raise VideoError('Source decode limit exceeded')
        if frame.pts is None or frame.time_base is None:raise VideoError('Video frame has no presentation timestamp')
        stamp=float(frame.pts*frame.time_base)-self.origin
        if stamp<self.previous_time-1e-6:raise VideoError('Source video timestamps are not monotonic')
        self.previous_time=stamp
        self.future=(frame,stamp)
    def get(self,stamp):
        while self.future and (self.current is None or self.future[1]<=stamp+1e-7):
            self.current=self.future;self.advance()
        if self.current is None:raise VideoError('Missing source video frame')
        frame,when=self.current
        if when>stamp+2/self.rate:raise VideoError('Source starts after requested trim')
        frame_duration=float(frame.duration*frame.time_base) if frame.duration and frame.time_base else 1/self.rate
        if self.ended and stamp>when+max(frame_duration,1/self.rate)+1e-6:raise VideoError('Source video ended before the requested clip')
        picture=frame.to_image().convert('RGB')
        if frame.rotation not in (-180,-90,0,90,180):raise VideoError('Unsupported frame rotation')
        if frame.rotation:picture=picture.rotate(frame.rotation,expand=True)
        return picture,when
    def close(self):self.context.__exit__(None,None,None)


def mix_source(mix,path,source_start,duration,timeline_start,gain,strict,duck=None):
    import av
    import numpy as np
    requested=samples(duration);offset=samples(timeline_start);end=offset+requested
    if offset<0 or end>mix.shape[1]:raise VideoError('Audio selection exceeds timeline; refusing silent truncation')
    written=0;last_end=0;decoded=0
    with media_open(path) as c:
        if not c.streams.audio:raise VideoError('Selected audio source has no audio stream')
        stream=c.streams.audio[0];stream.codec_context.thread_count=2
        origin=float(c.start_time or 0)/1000000
        if source_start>0:c.seek(max(0,int((source_start+origin)*1000000)),backward=True,any_frame=False)
        resampler=av.AudioResampler(format='fltp',layout='stereo',rate=RATE)
        ended=False
        def place(frame):
            nonlocal written,last_end,ended
            if frame.pts is None or not frame.time_base:raise VideoError('Audio frame has no presentation timestamp')
            at=round((float(frame.pts*frame.time_base)-origin-source_start)*RATE)
            data=frame.to_ndarray();left=max(0,-at);right=min(frame.samples,requested-at,end-offset-at)
            if at>=requested:ended=True;return
            if right>left:
                destination=at+left
                if destination<last_end:raise VideoError('Overlapping source audio timestamps')
                chunk=data[:,left:right]*gain
                if duck is not None:chunk=chunk*duck[offset+destination:offset+at+right]
                mix[:,offset+destination:offset+at+right]+=chunk
                written+=right-left;last_end=at+right
        for decoded_frame in c.decode(audio=0):
            decoded+=1
            if decoded>30000:raise VideoError('Audio decode frame limit exceeded')
            for converted in resampler.resample(decoded_frame):place(converted)
            if ended:break
        if not ended:
            for converted in resampler.resample(None):place(converted)
    missing=max(0,min(requested,end-offset)-written)
    if strict and missing>2048:raise VideoError('Imported audio ended before its requested duration')
    return {'requested_samples':requested,'mixed_source_samples':written,'uncovered_samples':missing,'gain':gain,'ducked_music':duck is not None}


def compose_audio(spec,effective,assets,metadata):
    import numpy as np
    mix=np.zeros((2,samples(effective['duration'])),dtype=np.float32);tracks=[]
    duck=np.ones(mix.shape[1],dtype=np.float32);narration_spans=[];ramp=round(.05*RATE)
    for source in spec['audio']:
        if source['role']!='narration' or source['gain']==0:continue
        begin=samples(source['start']);end=begin+samples(source['duration'])
        if end>mix.shape[1]:raise VideoError('Narration exceeds effective timeline; refusing silent truncation')
        left=max(0,begin-ramp);right=min(mix.shape[1],end+ramp)
        if begin>left:duck[left:begin]=np.minimum(duck[left:begin],np.linspace(1,.25,begin-left,endpoint=False,dtype=np.float32))
        duck[begin:end]=np.minimum(duck[begin:end],.25)
        if right>end:duck[end:right]=np.minimum(duck[end:right],np.linspace(.25,1,right-end,endpoint=False,dtype=np.float32))
        narration_spans.append({'id':source['id'],'start':begin/RATE,'end':end/RATE})
    for clip,timing in zip(spec['clips'],effective['clips']):
        if not clip['muted'] and clip['gain']>0 and metadata[clip['asset_id']]['has_audio']:
            stat=mix_source(mix,assets[clip['asset_id']],timing['source_start'],timing['effective_duration'],timing['timeline_start_frame']/spec['fps'],clip['gain'],False)
            tracks.append({'id':clip['id'],'role':'original_clip',**stat})
    for source in spec['audio']:
        if source['gain']==0:continue
        start=samples(source['start'])/RATE;length=samples(source['duration'])/RATE
        source_start=samples(source['source_start'])/RATE
        stat=mix_source(mix,assets[source['asset_id']],source_start,length,start,source['gain'],True,duck if source['role']=='music' and narration_spans else None)
        tracks.append({'id':source['id'],'role':source['role'],'effective_start':start,'effective_source_start':source_start,'effective_duration':length,**stat})
    peak=float(np.max(np.abs(mix))) if mix.size else 0.;clipped=int(np.count_nonzero(np.abs(mix)>1))
    np.clip(mix,-1,1,out=mix)
    return mix if tracks else None,{'tracks':tracks,'timing':'Imported audio start, source_start and duration independently round decimal half-up to the nearest 48 kHz sample. No video-frame rounding or implicit tail truncation. Original clip audio follows its frame-quantized video trim.','sample_rate':RATE,'channels':2,'pre_clip_peak':peak,
        'ducking':{'music_gain_multiplier':.25,'ramp_seconds':.05,'narration_spans':narration_spans,'trigger':'Explicit narration track schedule only; not detected speech or word alignment.'},
        'hard_clipped_channel_samples':clipped,'hard_clipped_fraction':clipped/mix.size if mix.size else 0,
        'notice':'Imported audio only. Gain and additive mixing; peaks over full scale are clipped and explicitly counted. No generated voice or loudness-normalization claim.'}


def render_texts(spec,folder):
    from PIL import Image
    if not spec['texts']:return [],{'success':True,'layers':[],'source_unchanged':True,'font_bytes_verified':True}
    job=folder/'text-job.json';job.write_text(packed({'width':spec['width'],'height':spec['height'],'texts':spec['texts']}),encoding='utf-8')
    result=subprocess.run(['node',str(ROOT/'scripts/video_render_text.mjs'),str(job),str(folder)],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=75,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    path=folder/'text-report.json'
    if result.returncode or not path.is_file() or path.stat().st_size>1024**2:raise VideoError('Exact text renderer failed: '+result.stderr[-1500:].decode('utf-8',errors='replace'))
    report=json.loads(path.read_text(encoding='utf-8'))
    if not report.get('success'):raise VideoError('Exact text layout failed; adjust text box/font size instead of truncating')
    overlays=[]
    for index,text in enumerate(spec['texts']):
        path=folder/f'text-{index:03d}.png'
        if not path.is_file() or path.stat().st_size>6*1024**2:raise VideoError('Text raster exceeds size bound')
        with Image.open(path) as im:
            if im.size!=(spec['width'],spec['height']):raise VideoError('Unexpected text raster dimensions')
            overlays.append((frames(text['start'],spec['fps']),frames(text['end'],spec['fps']),im.convert('RGBA').copy()))
    return overlays,report


def subtitle_files(texts,fps,folder):
    import html
    def stamp(seconds,vtt=False):
        ms=round(seconds*1000);hours,ms=divmod(ms,3600000);minutes,ms=divmod(ms,60000);seconds,ms=divmod(ms,1000)
        return f'{hours:02}:{minutes:02}:{seconds:02}'+('.' if vtt else ',')+f'{ms:03}'
    selected=sorted([t for t in texts if t['kind']=='subtitle'],key=lambda t:(t['start'],t['end']))
    srt=[];vtt=['WEBVTT\n']
    for index,text in enumerate(selected,1):
        start=frames(text['start'],fps)/fps;end=frames(text['end'],fps)/fps
        srt.append(f'{index}\n{stamp(start)} --> {stamp(end)}\n'+text['text']+'\n')
        vtt.append(f'{index}\n{stamp(start,True)} --> {stamp(end,True)}\n'+html.escape(text['text'],quote=False)+'\n')
    (folder/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')
    (folder/'subtitles.vtt').write_text('\n'.join(vtt),encoding='utf-8')
    return {'cues':len(selected),'timing':'Same quantized start-inclusive/end-exclusive times as raster overlays.',
            'notice':'Burned-in raster is the exact-text reference. External subtitle players may interpret SRT markup or multiline cue boundaries differently.'}


def fit_picture(picture,spec,motion,position):
    from PIL import Image
    w,h=spec['width'],spec['height'];scale=min(w/picture.width,h/picture.height)
    # Image-only motion keeps the whole source visible; it never invents motion.
    if motion=='pan_zoom':scale*=.92+.08*position
    size=(max(1,round(picture.width*scale)),max(1,round(picture.height*scale)))
    picture=picture.resize(size,Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',(w,h),spec['background'])
    horizontal=.4+.2*position if motion=='pan_zoom' else .5
    canvas.alpha_composite(picture.convert('RGBA'),(round((w-size[0])*horizontal),(h-size[1])//2))
    return canvas.convert('RGB')


def qc_movie(path,expected_frames,fps,audio_samples,progress):
    import numpy as np
    frames_seen=0;audio_seen=0;black=0;identical=0;previous_hash=None;last_time=-1.;peak=0.;squares=0.;channels_seen=0
    with media_open(path) as c:
        if len(c.streams.video)!=1 or len(c.streams.audio)!=(1 if audio_samples else 0):raise VideoError('Output stream count mismatch')
        video=c.streams.video[0];video.codec_context.thread_count=2
        if video.codec_context.name!='h264' or video.codec_context.format.name!='yuv420p':raise VideoError('Output H264/yuv420p contract failed')
        if c.streams.audio and c.streams.audio[0].codec_context.name!='aac':raise VideoError('Output AAC contract failed')
        for packet in c.demux():
            for frame in packet.decode():
                if packet.stream.type=='video':
                    if frame.pts is None:raise VideoError('Output frame lacks timestamp')
                    stamp=float(frame.pts*frame.time_base)
                    if stamp<=last_time:raise VideoError('Non-monotonic output video time')
                    last_time=stamp;frames_seen+=1
                    if frames_seen>expected_frames:raise VideoError('Unexpected extra output frame')
                    tiny=np.asarray(frame.to_image().resize((32,18)))
                    black+=int(float(tiny.mean())<2.0);current=sha(tiny.tobytes());identical+=int(current==previous_hash);previous_hash=current
                    if frames_seen%max(1,fps)==0:progress({'stage':'qc','step':frames_seen,'total':expected_frames})
                elif packet.stream.type=='audio':
                    samples=frame.to_ndarray().astype(np.float64)
                    if frame.format.name not in ('fltp','flt'):raise VideoError('Unexpected decoded AAC sample format')
                    audio_seen+=frame.samples;channels_seen+=samples.size
                    peak=max(peak,float(np.abs(samples).max(initial=0)));squares+=float(np.sum(samples*samples))
        if frames_seen!=expected_frames:raise VideoError('Output frame count mismatch')
        if audio_samples and not audio_samples-1024<=audio_seen<=audio_samples+2048:raise VideoError('Output audio duration mismatch')
    # MP4 fast-start is checked on actual top-level boxes, not the requested flag.
    positions={};length=Path(path).stat().st_size
    with Path(path).open('rb') as f:
        offset=0
        while offset+8<=length:
            f.seek(offset);header=f.read(8);size=int.from_bytes(header[:4],'big');name=header[4:].decode('ascii',errors='replace')
            if size==1:size=int.from_bytes(f.read(8),'big')
            if size==0:size=length-offset
            if size<8 or offset+size>length:raise VideoError('Malformed MP4 box')
            positions.setdefault(name,offset);offset+=size
    faststart='moov' in positions and 'mdat' in positions and positions['moov']<positions['mdat']
    if not faststart:raise VideoError('MP4 is not fast-start')
    return {'success':True,'full_decode':True,'video_frames':frames_seen,'expected_frames':expected_frames,
        'duration':expected_frames/fps,'last_video_timestamp':last_time,'faststart':faststart,
        'audio_samples_decoded':audio_seen,'audio_samples_requested':audio_samples,'audio_peak':peak,
        'audio_rms':math.sqrt(squares/channels_seen) if channels_seen else None,'black_thumbnail_frames':black,
        'identical_adjacent_thumbnails':identical,'notice':'Black/frozen counts are diagnostics, not failures: still images and black backgrounds can be intentional. Timing checks do not establish lip synchronization, speech quality or semantic quality.'}


def render_local(payload,folder,progress):
    import av
    import numpy as np
    from PIL import Image,ImageOps
    folder=Path(folder).resolve();keys(payload,{'spec','assets'},{'spec','assets'},'job');spec=payload['spec'];effective=validate_spec(spec)
    if not isinstance(payload['assets'],dict) or len(payload['assets'])>16:raise VideoError('Too many materialized assets')
    assets={};metadata={};bytes_total=0
    required={c['asset_id'] for c in spec['clips']}|{a['asset_id'] for a in spec['audio']}
    if not required.issubset(payload['assets']):raise VideoError('Missing referenced asset')
    for asset_id in required:
        ident(asset_id);entry=payload['assets'][asset_id]
        if not isinstance(entry,dict):raise VideoError('Invalid materialized asset')
        path=local_asset(entry.get('path'),folder);assets[asset_id]=path;metadata[asset_id]=probe_local(path);bytes_total+=path.stat().st_size
    if bytes_total>128*1024**2:raise VideoError('Input total exceeds 128 MiB')
    for clip,timing in zip(spec['clips'],effective['clips']):
        data=metadata[clip['asset_id']]
        if data['kind'] not in ('image','video'):raise VideoError('Timeline clip requires image or video')
        if data['kind']=='video':
            if clip['motion']!='none':raise VideoError('pan_zoom is only an explicit still-image animation')
            if timing['source_start']+timing['effective_duration']>data['duration']+1/spec['fps']:raise VideoError('Clip extends beyond source duration')
        elif clip['start']!=0:raise VideoError('Still image trim start must be zero')
    for audio in spec['audio']:
        data=metadata[audio['asset_id']]
        if not data['has_audio'] or samples(audio['source_start'])+samples(audio['duration'])>samples(data['duration'])+1:raise VideoError('Audio selection extends beyond its source')
    progress({'stage':'prepare','step':0,'total':effective['frames']})
    overlays,text_report=render_texts(spec,folder)
    mix,audio_report=compose_audio(spec,effective,assets,metadata)
    subtitles=subtitle_files(spec['texts'],spec['fps'],folder)
    final=folder/'movie.mp4';partial=folder/'movie.partial.mp4'
    if final.exists() or partial.exists():raise VideoError('Refusing to overwrite a rendered movie')
    encoded=0;audio_cursor=0;fps=spec['fps'];sampler=None
    with av.open(str(partial),'w',format='mp4',options={'movflags':'+faststart'}) as container:
        video=container.add_stream('libx264',rate=fps);video.width=spec['width'];video.height=spec['height'];video.pix_fmt='yuv420p'
        video.codec_context.thread_count=2;video.codec_context.options={'preset':'veryfast','crf':'23','tune':'zerolatency'}
        audio=None
        if mix is not None:
            audio=container.add_stream('aac',rate=RATE);audio.codec_context.layout='stereo';audio.codec_context.format='fltp';audio.bit_rate=128000
        def add_audio(until):
            nonlocal audio_cursor
            if audio is None:return
            while audio_cursor<min(until,mix.shape[1]):
                end=min(audio_cursor+1024,mix.shape[1]);frame=av.AudioFrame.from_ndarray(np.ascontiguousarray(mix[:,audio_cursor:end]),format='fltp',layout='stereo')
                frame.sample_rate=RATE;frame.time_base=Fraction(1,RATE);frame.pts=audio_cursor
                for packet in audio.encode(frame):container.mux(packet)
                audio_cursor=end
        try:
            for clip,timing in zip(spec['clips'],effective['clips']):
                path=assets[clip['asset_id']];data=metadata[clip['asset_id']];image=None
                if data['kind']=='image':
                    with Image.open(path) as im:image=ImageOps.exif_transpose(im).convert('RGBA').copy()
                else:sampler=VideoSampler(path,timing['source_start'])
                for index in range(timing['frames']):
                    source=image if image is not None else sampler.get(timing['source_start']+index/fps)[0]
                    canvas=fit_picture(source,spec,clip['motion'],index/max(1,timing['frames']-1)).convert('RGBA')
                    for start,end,overlay in overlays:
                        if start<=encoded<end:canvas.alpha_composite(overlay)
                    rgb=canvas.convert('RGB')
                    if encoded==0:rgb.save(folder/'poster.png',format='PNG')
                    frame=av.VideoFrame.from_image(rgb);frame.pts=encoded;frame.time_base=Fraction(1,fps)
                    for packet in video.encode(frame):container.mux(packet)
                    encoded+=1;add_audio(round(encoded/fps*RATE))
                    if encoded%max(1,fps//2)==0 or encoded==effective['frames']:progress({'stage':'encode','step':encoded,'total':effective['frames']})
                    if partial.stat().st_size>MAX_OUTPUT:raise VideoError('Encoded output exceeds 64 MiB')
                if sampler:sampler.close();sampler=None
            for packet in video.encode():container.mux(packet)
            if audio is not None:
                add_audio(mix.shape[1])
                for packet in audio.encode():container.mux(packet)
        finally:
            if sampler:sampler.close()
    if partial.stat().st_size>MAX_OUTPUT:raise VideoError('Encoded output exceeds 64 MiB')
    qc=qc_movie(partial,effective['frames'],fps,mix.shape[1] if mix is not None else 0,progress)
    partial.replace(final)
    warnings=['Cuts only. Images are composited sources; pan_zoom is still-image animation, not generated scenes.','Review typography, timing, audio and picture quality in the actual exported movie.']
    if audio_report['hard_clipped_channel_samples']:warnings.append('Audio mix clipped samples; reduce gain before publication.')
    return {'success':True,'version':VERSION,'engine':'PyAV with local FFmpeg libraries; ffmpeg/ffprobe CLI unavailable',
        'pyav_version':av.__version__,'ffmpeg_version':av.ffmpeg_version_info,'library_versions':av.library_versions,
        'width':spec['width'],'height':spec['height'],'fps':fps,'duration':effective['duration'],'frames':encoded,
        'timing':effective,'inputs':metadata,'text':text_report,'audio':audio_report,'subtitles':subtitles,'qc':qc,
        'files':{name:{'bytes':(folder/name).stat().st_size,'sha256':sha((folder/name).read_bytes())} for name in ('movie.mp4','poster.png','subtitles.srt','subtitles.vtt')},
        'needs_review':True,'audio_quality_passed':False if audio_report['hard_clipped_channel_samples'] else None,
        'warnings':warnings,'network':'File-object demuxing, restricted formats, denied nested IO and Python socket guard; not an OS firewall.'}
