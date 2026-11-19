"""Versioned, bounded non-destructive video edits. These are not model prompts."""
from typing import Literal
from pydantic import Field, model_validator
from backend.visuals import Input, TextLayer, COLOR

VERSION = 'riwaq-video-v1'
MAX_ASSET = 8 * 1024**2
PROJECT_BYTES = 256 * 1024**2
GLOBAL_BYTES = 1024**3
MAX_OUTPUT = 64 * 1024**2
MAX_SECONDS = 60
SIZES = {(1280,720),(720,1280),(640,360),(360,640),(960,540),(540,960)}

class Clip(Input):
    id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    asset_id: str = Field(min_length=1,max_length=80)
    start: float = Field(default=0,ge=0,le=120,allow_inf_nan=False)
    duration: float = Field(ge=.1,le=60,allow_inf_nan=False)
    muted: bool = True
    gain: float = Field(default=1,ge=0,le=2,allow_inf_nan=False)
    motion: Literal['none','pan_zoom'] = 'none'

class TimedText(TextLayer):
    kind: Literal['title','subtitle'] = 'title'
    start: float = Field(ge=0,le=60,allow_inf_nan=False)
    end: float = Field(gt=0,le=60,allow_inf_nan=False)
    @model_validator(mode='after')
    def timing(self):
        if self.end <= self.start: raise ValueError('Text end must follow start')
        if len(self.text)>1000: raise ValueError('One video text layer is limited to 1000 characters')
        return self

class AudioTrack(Input):
    id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    asset_id: str = Field(min_length=1,max_length=80)
    start: float = Field(ge=0,le=60,allow_inf_nan=False)
    source_start: float = Field(default=0,ge=0,le=120,allow_inf_nan=False)
    duration: float = Field(ge=.1,le=60,allow_inf_nan=False)
    gain: float = Field(default=1,ge=0,le=2,allow_inf_nan=False)
    role: Literal['narration','music','effect'] = 'narration'

class ProductionBrief(Input):
    goal: str = Field(default='',max_length=1000)
    audience: str = Field(default='',max_length=300)
    style: str = Field(default='',max_length=1000)
    voice_language: str = Field(default='ar',max_length=30)
    text_language: str = Field(default='ar',max_length=30)
    reference_notes: str = Field(default='',max_length=1500)

class Timeline(Input):
    width: int = 1280
    height: int = 720
    fps: Literal[24,30] = 24
    background: str = Field(default='#172B2A',pattern=COLOR)
    clips: list[Clip] = Field(default_factory=list,max_length=12)
    texts: list[TimedText] = Field(default_factory=list,max_length=24)
    audio: list[AudioTrack] = Field(default_factory=list,max_length=4)
    brief: ProductionBrief = Field(default_factory=ProductionBrief)
    @model_validator(mode='after')
    def bounds(self):
        if (self.width,self.height) not in SIZES: raise ValueError('Unsupported canvas preset')
        entries=[*self.clips,*self.texts,*self.audio]
        if len({e.id for e in entries})!=len(entries): raise ValueError('Timeline element IDs must be unique')
        duration=sum(c.duration for c in self.clips)
        if duration>MAX_SECONDS+1e-7: raise ValueError('Timeline exceeds 60 seconds')
        if sum(len(t.text) for t in self.texts)>12000: raise ValueError('Text source limit exceeded')
        for t in self.texts:
            if t.x+t.width>self.width or t.y+t.height>self.height: raise ValueError('Text region exceeds canvas')
            if t.end>duration+1e-7: raise ValueError('Text extends beyond timeline')
        for a in self.audio:
            if a.start+a.duration>duration+1e-7: raise ValueError('Audio extends beyond timeline; trim explicitly')
        return self

class CreateTimeline(Input):
    title: str = Field(min_length=1,max_length=120)
    spec: Timeline
    @model_validator(mode='after')
    def title_valid(self):
        if not self.title.strip() or any(ord(c)<32 for c in self.title): raise ValueError('Invalid title')
        return self

class UpdateTimeline(CreateTimeline):
    expected_version: int = Field(ge=1,le=100)

class RestoreTimeline(Input):
    expected_version: int = Field(ge=1,le=100)
    from_version: int = Field(ge=1,le=100)

class ExportInput(Input):
    timeline_id: str = Field(min_length=1,max_length=80)
    version: int = Field(ge=1,le=100)
    idempotency_key: str = Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

def asset_ids(spec):
    return {x['asset_id'] for x in [*spec['clips'],*spec['audio']]}

def subtitles(spec, fmt='srt'):
    """Literal Unicode source and explicit user times, never claimed as speech alignment."""
    from decimal import Decimal, ROUND_HALF_UP
    def stamp(seconds):
        frame=int((Decimal(str(seconds))*spec['fps']).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        ms=round(frame/spec['fps']*1000);s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60)
        return f'{h:02}:{m:02}:{s:02}'+(',' if fmt=='srt' else '.')+f'{ms:03}'
    out=['WEBVTT\n'] if fmt=='vtt' else []
    for i,t in enumerate(sorted((t for t in spec['texts'] if t['kind']=='subtitle'),key=lambda t:t['start']),1):
        # VTT interprets markup. Escape it so exported source retains its literal visible meaning.
        text=t['text']
        if fmt=='vtt': text=text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        out.append(f"{i}\n{stamp(t['start'])} --> {stamp(t['end'])}\n{text}\n")
    return '\n'.join(out)
