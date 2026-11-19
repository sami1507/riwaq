"""Server-owned task authority and bounded model output contracts."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from backend.coding_sandbox import TestRequest
from backend.workspace import relative_parts

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class WorkspaceInput(Strict):
    path:str=Field(min_length=1,max_length=2048)
    label:str=Field(default='',max_length=120)

class InspectInput(Strict):
    selected_paths:list[str]=Field(default_factory=list,max_length=12)
    query:str=Field(default='',max_length=1000)

class TaskInput(Strict):
    request_key:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    mode:Literal['explain','review','diagnose','edit','tests','docs','dependency']='explain'
    authority:Literal['read_only','propose','apply_local']='read_only'
    language:str=Field(default='ar',min_length=2,max_length=35,pattern=r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$')
    prompt:str=Field(min_length=1,max_length=4000)
    acceptance:str=Field(default='',max_length=1200)
    selected_paths:list[str]=Field(min_length=1,max_length=12)
    test_request:TestRequest|None=None
    @field_validator('selected_paths')
    @classmethod
    def paths(cls,values):
        for value in values:relative_parts(value)
        if len({x.casefold() for x in values})!=len(values):raise ValueError('Duplicate paths')
        return values
    @model_validator(mode='after')
    def consent(self):
        if self.mode in ('explain','review','diagnose') and self.authority!='read_only':raise ValueError('Analysis modes require read_only authority')
        if self.mode in ('edit','tests','docs','dependency') and self.authority=='read_only':raise ValueError('Choose proposal or local-edit authority for a change task')
        if not self.prompt.strip():raise ValueError('A concrete request is required')
        if self.language.lower()=='auto':raise ValueError('Select a response language')
        return self

class Confirmation(Strict):
    confirm:Literal[True]
    expected_revision:int=Field(ge=1)

class Resume(Strict):
    request_key:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

class Reference(Strict):
    path:str=Field(min_length=1,max_length=1024)
    line_start:int=Field(ge=1,le=1000000)
    line_end:int=Field(ge=1,le=1000000)

class Hunk(Strict):
    path:str=Field(min_length=1,max_length=1024)
    before:str=Field(min_length=1,max_length=4000)
    after:str=Field(max_length=8000)

class ModelResult(Strict):
    answer:str=Field(min_length=1,max_length=8000)
    refs:list[Reference]=Field(min_length=1,max_length=12)
    edits:list[Hunk]=Field(max_length=8)

MODES=[{'id':key,'label_ar':ar,'label_en':en,'available':True} for key,ar,en in (
 ('explain','شرح','Explain'),('review','مراجعة','Review'),('diagnose','تشخيص','Diagnose'),
 ('edit','إصلاح أو ميزة محددة','Fix or small feature'),('tests','كتابة اختبارات','Write tests'),
 ('docs','توثيق','Documentation'),('dependency','مراجعة اعتماد أو تعديله','Dependency change'))]+[
 {'id':'create','label_ar':'إنشاء مشروع جديد — غير متاح بعد','label_en':'New project creation — not yet implemented','available':False}]
LANGUAGES=[{'id':name,'read':'utf8_text','analyze':'bounded_python_ast' if name=='Python' else 'heuristic_or_text','edit':'reviewable_exact_hunks','format':False,'build':False,'test':False,'run':False,'note':'Availability of an SDK or successful isolated execution is not inferred from source syntax.'} for name in ('Python','JavaScript','TypeScript','Java','Kotlin','C','C++','C#','Go','Rust','PHP','SQL','Shell','Other UTF-8 text')]
