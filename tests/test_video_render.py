"""Renderer contract tests plus an explicit real, supervised media smoke CLI.

Run as a script to produce an actual receipt; ordinary pytest never runs a model.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import threading
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pytest
from backend import video_render as render
from backend.video_schema import Timeline


def base_spec(clips):
    return Timeline(width=640,height=360,clips=clips,texts=[],audio=[]).model_dump()


def clip(identifier,asset,duration=1,start=0,muted=True,motion='none'):
    return {'id':identifier,'asset_id':asset,'start':start,'duration':duration,'muted':muted,'gain':1,'motion':motion}


def test_root_timeline_schema_matches_worker_including_defaults():
    spec=base_spec([clip('c','image')])
    assert 'brief' in spec
    assert render.validate_spec(spec)['frames']==24


@pytest.mark.parametrize('mutate',[
    lambda s:s.update(width=639),
    lambda s:s.update(fps=True),
    lambda s:s['clips'][0].update(duration=float('nan')),
    lambda s:s['clips'][0].update(motion='generated_scene'),
    lambda s:s['clips'].append(dict(s['clips'][0])),
    lambda s:s['clips'][0].update(duration=61),
    lambda s:s.update(shell='ffmpeg'),
])
def test_invalid_timeline_fails_before_media_io(mutate):
    spec=base_spec([clip('c','image')]);mutate(spec)
    with pytest.raises(render.VideoError):render.validate_spec(spec)


def test_fractional_times_use_half_up_not_binary_bankers_rounding():
    assert render.frames(.0625,24)==2
    spec=base_spec([clip('c','image',1.02)])
    value=render.validate_spec(spec)
    assert value['frames']==24 and value['duration']==1
    assert value['clips'][0]['requested_duration']==1.02


def test_asset_directory_and_nested_reference_fence(tmp_path):
    inside=tmp_path/'owned';inside.mkdir();foreign=tmp_path/'secret';foreign.write_bytes(b'local secret')
    with pytest.raises(render.VideoError):render.local_asset(str(foreign),inside)
    with pytest.raises(render.VideoError):render.local_asset('https://example.com/source.mp4',inside)
    with pytest.raises(render.VideoError):render.deny_extra_io('file:///private',0,{})
    playlist=inside/'playlist.mp4';playlist.write_text('#EXTM3U\nhttps://example.com/stream\n')
    with pytest.raises(render.VideoError):render.probe_local(playlist)


def test_actual_png_probe_and_strict_animation_policy(tmp_path):
    from PIL import Image
    picture=tmp_path/'source.png';Image.new('RGB',(64,32),'red').save(picture)
    data=render.probe_local(picture)
    assert data['kind']=='image' and data['width']==64 and data['height']==32 and not data['has_audio']
    assert data['sha256']==render.sha(picture.read_bytes())


def test_image_animation_preserves_source_fit():
    from PIL import Image
    picture=Image.new('RGB',(100,50),'red');spec=base_spec([clip('c','image')])
    canvas=render.fit_picture(picture,spec,'pan_zoom',.5)
    assert canvas.size==(640,360)
    # The whole image remains inside the output, with letterboxing rather than distortion/cropping.
    assert canvas.getpixel((0,0))==(23,43,42)
    assert canvas.getpixel((320,180))==(255,0,0)


def test_transparent_image_composites_onto_declared_background():
    from PIL import Image
    picture=Image.new('RGBA',(64,64),(255,0,0,0));spec=base_spec([clip('c','image')])
    canvas=render.fit_picture(picture,spec,'none',0)
    assert canvas.getpixel((320,180))==(23,43,42)


def test_cumulative_quantization_keeps_full_timeline_text_boundaries():
    spec=base_spec([clip('c'+str(i),'image',.14) for i in range(12)])
    effective=render.validate_spec(spec)
    assert effective['frames']==render.frames(1.68,24)==40
    assert sum(c['frames'] for c in effective['clips'])==40



def test_imported_audio_preserves_sample_precision_and_narration_ducking(monkeypatch):
    spec=base_spec([clip('c','image',18)])
    spec['audio']=[{'id':'voice','asset_id':'audio','start':.003,'source_start':.007,
                    'duration':17.429,'gain':1,'role':'narration'}]
    effective=render.validate_spec(spec);observed=[]
    def capture(mix,path,source_start,duration,timeline_start,gain,strict,duck=None):
        observed.append((source_start,duration,timeline_start))
        return {'requested_samples':render.samples(duration),'mixed_source_samples':render.samples(duration),'uncovered_samples':0}
    monkeypatch.setattr(render,'mix_source',capture)
    _,report=render.compose_audio(spec,effective,{'audio':'unused'}, {})
    assert observed==[(.007,17.429,.003)]
    assert report['tracks'][0]['requested_samples']==836592
    assert report['ducking']['narration_spans']==[{'id':'voice','start':.003,'end':17.432}]
    assert render.samples(.00003125)==2  # decimal half-up, not banker rounding


def test_audio_tail_cannot_hide_in_video_frame_rounding():
    spec=base_spec([clip('c','image',1)])
    spec['audio']=[{'id':'voice','asset_id':'audio','start':0,'source_start':0,
                    'duration':1.012,'gain':1,'role':'narration'}]
    with pytest.raises(render.VideoError,match='48 kHz'):
        render.validate_spec(spec)
    import numpy as np
    with pytest.raises(render.VideoError,match='silent truncation'):
        render.mix_source(np.zeros((2,48000)),None,0,1.012,0,1,True)

def tone(path,frequency,seconds=2):
    import numpy as np
    values=(.15*np.sin(2*np.pi*frequency*np.arange(round(seconds*48000))/48000)*32767).astype('<i2')
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000);out.writeframes(values.tobytes())


def smoke(directory):
    from PIL import Image
    import av
    from backend import video_capabilities
    folder=Path(directory).resolve();folder.mkdir(parents=True,exist_ok=True)
    if (folder/'base').exists() or (folder/'composite').exists():raise RuntimeError('Use a new smoke directory; existing evidence is not overwritten')
    base=folder/'base';base.mkdir();final=folder/'composite';final.mkdir()
    for name,color in [('red',(220,30,30)),('blue',(30,30,220))]:Image.new('RGB',(320,180),color).save(base/(name+'.png'))
    tone(base/'tone.wav',440)
    source=base_spec([clip('red-clip','red'),clip('blue-clip','blue')])
    source['audio']=[{'id':'source-audio','asset_id':'tone','start':0,'source_start':0,'duration':2,'gain':.5,'role':'effect'}]
    payload={'spec':source,'assets':{name:{'path':str(base/(name+ext))} for name,ext in [('red','.png'),('blue','.png'),('tone','.wav')]}}
    progress=[]
    first=render.render(payload,base,threading.Event(),lambda event:progress.append(event))
    assert first['qc']['full_decode'] and first['qc']['video_frames']==48 and first['qc']['audio_samples_decoded']>0
    (final/'source.mp4').write_bytes((base/'movie.mp4').read_bytes())
    Image.new('RGB',(200,300),(24,120,76)).save(final/'portrait.png');tone(final/'narration.wav',660);tone(final/'music.wav',220)
    probes={name:render.probe(final/(name+extension),final,threading.Event()) for name,extension in [('source','.mp4'),('narration','.wav')]}
    assert probes['source']['kind']=='video' and probes['source']['has_audio'] and probes['source']['codec']=='h264'
    spec=base_spec([clip('trimmed-video','source',1,.5,False),clip('still-animation','portrait',1,motion='pan_zoom')])
    spec['texts']=[{'id':'arabic-title','kind':'title','text':'رواق — تجربة فيديو','start':0,'end':2,
        'x':25,'y':20,'width':590,'height':80,'language':'ar','direction':'rtl','font':'arabic',
        'size':28,'weight':600,'color':'#FFFFFF','align':'right','protected':True},
       {'id':'english-subtitle','kind':'subtitle','text':'Imported clips · literal text','start':.5,'end':1.5,
        'x':25,'y':280,'width':590,'height':65,'language':'en','direction':'ltr','font':'latin',
        'size':20,'weight':400,'color':'#FFFFFF','align':'center','protected':True}]
    spec['audio']=[{'id':'narration','asset_id':'narration','start':.25,'source_start':0,'duration':1,'gain':.5,'role':'narration'},
                   {'id':'music','asset_id':'music','start':0,'source_start':0,'duration':2,'gain':.25,'role':'music'}]
    assets={name:{'path':str(final/(name+extension))} for name,extension in [('source','.mp4'),('portrait','.png'),('narration','.wav'),('music','.wav')]}
    second=render.render({'spec':spec,'assets':assets},final,threading.Event(),lambda event:progress.append(event))
    assert second['qc']['full_decode'] and second['qc']['video_frames']==48 and second['qc']['faststart']
    assert second['text']['source_unchanged'] and second['text']['font_bytes_verified']
    assert len(second['audio']['tracks'])==3 and second['audio']['ducking']['narration_spans']
    assert second['qc']['audio_rms']>0 and second['audio']['hard_clipped_channel_samples']==0
    assert any(e.get('stage')=='encode' and e.get('step')==48 for e in progress)
    assert 'Imported clips · literal text' in (final/'subtitles.srt').read_text(encoding='utf-8')
    # Verify trim/cut content from actual decoded frames in a region without overlays.
    with render.media_open(final/'movie.mp4') as container:
        frames=list(container.decode(video=0))
        first_pixel=frames[0].to_image().getpixel((320,180));later_pixel=frames[18].to_image().getpixel((320,180))
        assert first_pixel[0]>first_pixel[2]+80 and later_pixel[2]>later_pixel[0]+80
    report={'success':True,'pyav_version':av.__version__,'implementation_sha256':video_capabilities.implementation_fingerprint(),
      'qc':{'full_decode':True,'audio_mix':True,'trim_cut_pixels':True,'exact_text_source':True},
      'scope':'Controlled procedural color and sine-wave fixtures. Actual supervised FFmpeg-library encode/decode and Chromium text; not generated video or TTS, not independent quality review.',
      'paths':{'base':str(base),'composite':str(final)},'runs':[first,second],'probes':probes,'progress':progress,
      'requires_manual_visual_audio_review':True}
    path=ROOT/'reports/video-render-smoke.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'success':True,'report':str(path),'movie':str(final/'movie.mp4'),'frames':second['frames'],'qc':report['qc']}))
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-dir',required=True)
    smoke(parser.parse_args().smoke_dir)
