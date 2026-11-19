"""Bounded skill planning. Plans and model instructions never grant tool rights."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from backend.research_schema import ResearchOptions
from backend.coding_sandbox import TestRequest
from backend.skill_files import SourceRef,OutputVersion,VersionRequest,FolderRequest
from backend.skill_data_revision import DataReport,DataRevision

ROOT = Path(__file__).resolve().parents[1]
MAX_ACTIVE = 8
MAX_INSTRUCTION_CHARS = 14000

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class ArtifactRequest(Strict):
    kind: Literal['xlsx','docx','pptx']
    payload: dict[str, Any]

class SpeechInput(Strict):
    voiceId: str = Field(pattern=r'^sapi-[a-f0-9]{32}$')
    language: str = Field(min_length=2,max_length=35,pattern=r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')
    rate: int = Field(default=0,ge=-5,le=5)


class ReportRevision(Strict):
    expectedResultHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    targetLanguage: Literal['ar','en','he']


class SkillInputs(Strict):
    text: str = Field(default='', max_length=16000)
    targetLanguage: str = Field(default='', max_length=80)
    reportRevision: ReportRevision | None = None
    dataReport: DataReport | None = None
    dataRevision: DataRevision | None = None
    documentIds: list[str] = Field(default_factory=list, max_length=8)
    artifact: dict[str, Any] | None = None
    artifacts: list[ArtifactRequest] = Field(default_factory=list, max_length=3)
    researchQuestion: str = Field(default='', max_length=2000)
    researchOptions: ResearchOptions | None = None
    retrievalMode: Literal['lexical','hybrid'] = 'lexical'
    fileQuery: str = Field(default='',max_length=200)
    summaryLanguage: str = Field(default='',max_length=80)
    summaryMode: Literal['extractive','generative'] = 'extractive'
    speech: SpeechInput | None = None
    workspaceId: str = Field(default='',max_length=80)
    repositoryPaths: list[str] = Field(default_factory=list,max_length=12)
    memoryIds: list[str] = Field(default_factory=list,max_length=8)
    memoryExpectedVersions: dict[str,int] = Field(default_factory=dict,max_length=8)
    maxSteps: int = Field(default=8,ge=1,le=8)
    testRequest: TestRequest | None = None
    sourceRefs: list[SourceRef] = Field(default_factory=list,max_length=8)
    outputVersion: OutputVersion | None = None
    versionOperation: VersionRequest | None = None
    folderOperation: FolderRequest | None = None
    dataOperation: dict[str,Any] | None = None
    documentOperation: dict[str,Any] | None = None
    webOperation: dict[str,Any] | None = None
    planningOperation: dict[str,Any] | None = None
    scheduleOperation: dict[str,Any] | None = None
    mailOperation: dict[str,Any] | None = None
    meetingOperation: dict[str,Any] | None = None
    systemOperation: dict[str,Any] | None = None

    @field_validator('systemOperation')
    @classmethod
    def system_contract(cls,value):
        if value is None:return value
        from backend.skill_system import SystemInput
        return SystemInput.model_validate(value).model_dump()

    @field_validator('planningOperation','scheduleOperation','mailOperation','meetingOperation')
    @classmethod
    def productivity_contract(cls,value,info):
        if value is None:return value
        from backend.productivity_planning import PlanningRequest
        from backend.productivity_schedule import ScheduleRequest
        from backend.productivity_mail import MailInput
        from backend.productivity_meetings import MeetingInput
        model={'planningOperation':PlanningRequest,'scheduleOperation':ScheduleRequest,'mailOperation':MailInput,'meetingOperation':MeetingInput}[info.field_name]
        return model.model_validate(value).model_dump()

    @field_validator('sourceRefs')
    @classmethod
    def unique_sources(cls,values):
        if len({(v.kind,v.id,v.version) for v in values})!=len(values):raise ValueError('Duplicate source version')
        return values

    @field_validator('dataOperation','documentOperation','webOperation')
    @classmethod
    def operation_contract(cls,value,info):
        if value is None:return value
        if info.field_name=='dataOperation':
            from backend.skill_data import DataRequest
            return DataRequest.model_validate(value).model_dump()
        if info.field_name=='documentOperation':
            from backend.skill_documents import DocumentRequest
            return DocumentRequest.model_validate(value).model_dump()
        from backend.skill_web import WebInput
        return WebInput.model_validate(value).model_dump()

    @field_validator('repositoryPaths')
    @classmethod
    def scoped_paths(cls,values):
        from backend.workspace import relative_parts
        from fastapi import HTTPException
        try:
            for value in values: relative_parts(value)
        except HTTPException as exc:raise ValueError('Path is outside the allowed repository scope') from exc
        if len({x.casefold() for x in values})!=len(values): raise ValueError('Duplicate path')
        return values

    @field_validator('documentIds','memoryIds')
    @classmethod
    def resource_ids(cls,values):
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',x) for x in values):raise ValueError('Invalid resource identifier')
        if len(set(values))!=len(values):raise ValueError('Duplicate resource identifier')
        return values

    @field_validator('memoryExpectedVersions')
    @classmethod
    def versions(cls,values):
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',k) or type(v) is not int or v<1 for k,v in values.items()):raise ValueError('Invalid expected memory revision')
        return values

    @model_validator(mode='after')
    def unique_artifact_kinds(self):
        if self.artifact is not None and self.artifacts:
            raise ValueError('Use artifact or artifacts, not both')
        if len({x.kind for x in self.artifacts}) != len(self.artifacts):
            raise ValueError('Each artifact kind can be supplied only once')
        return self

class SkillRequest(Strict):
    request: str = Field(min_length=1, max_length=8000)
    requestedSkills: list[str] = Field(default_factory=list, max_length=MAX_ACTIVE)
    inputs: SkillInputs = Field(default_factory=SkillInputs)
    idempotencyKey: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    previousRunId: str | None = Field(default=None, max_length=80)

    @field_validator('request')
    @classmethod
    def meaningful_request(cls,value):
        if not value.strip():raise ValueError('Request must contain text')
        return value

class Intent(Strict):
    intent: list[str]
    goal: str
    constraints: list[str]
    requestedOutputs: list[str]
    references: list[str]
    ambiguities: list[str]
    basis: str

class SkillRun(Strict):
    id: str
    project_id: str
    request: str
    status: Literal['queued','running','cancelling','completed','partial','failed','cancelled','unknown']
    created_at: str
    updated_at: str
    intent: Intent
    selected_skills: list[str] = Field(max_length=MAX_ACTIVE)
    steps: list[dict[str, Any]] = Field(max_length=MAX_ACTIVE)
    criteria: list[dict[str, Any]] = Field(max_length=MAX_ACTIVE+1)
    result: dict[str, Any]
    receipt: dict[str, Any]
    policy_version: str
    error: str | None = None

# These bounded rules only propose workflows. Neither their result nor skill text
# grants authority. Keep identifiers and user content intact in the public Intent.
RULES = (
    ('explain', r'(?:و?اشرح(?:ه|ها)?|شرح|explain|הסבר)'),
    ('research', r'(?:و?ابحث|بحث|research|search|חפש)'),
    ('summarize', r'(?:و?لخص(?:ه|ها)?|تلخيص|summari[sz]e|סכם)'),
    ('translate', r'(?:و?(?:ت?ترجم)(?:ه|ها)?|ترجمة|translate|translation|ת?תרגם)'),
    ('create', r'(?:و?(?:انش[ئء]|ت?اعمل|تعمل|اصنع)(?:ه|ها)?|create|build|make|צור|וצור|תצור|הכן|והכן)'),
    ('modify', r'(?:و?(?:ت?اصلح|تصلح|ت?عدل|تغير)(?:ه|ها)?|تعديل|fix(?:ing)?|modify|edit|change|תקן|תתקן|תשנה)'),
    ('test', r'(?:و?اختبر(?:ه|ها)?|test|בדוק|(?:run|execute)\s+(?:the\s+)?tests|شغل\s+الاختبارات)'),
    ('review', r'(?:و?راجع(?:ه|ها)?|مراجعة|review|סקור)'),
    ('debug', r'(?:و?شخص|diagnose|diagnosis|debug)'),
)
OUTPUTS = (
    ('pptx', r'(?:عرض(?:ا)?|شرائح|slides?|presentation|pptx|מצגת)'),
    ('xlsx', r'(?:excel|xlsx|اكسل|مصنف|גיליון)'),
    ('docx', r'(?:word|docx|وورد|مستند|מסמך)'),
)
REFERENCE = re.compile(r'(?<![\w.])[\w./\\:-]+\.(?:pdf|docx|xlsx|pptx|py|tsx?|jsx?|md|json|java|rs|go|cs|cpp|sql)(?!\w)',re.I)
WRITING_SKILLS = {'code-generation','spreadsheet-creation','docx-authoring','slide-authoring','memory-correction','persistent-memory','memory-deletion','document-ingestion'}
WRITING_SKILLS |= {'pdf-creation','pdf-form-filling','pdf-merge-split','pdf-redaction','docx-editing','template-population',
                  'file-conversion','folder-organization','artifact-versioning','formula-debugging','spreadsheet-cleaning','spreadsheet-charts','web-form-filling'}


def _normalized(text):
    # Matching only: never substitute this normalization into filenames or output.
    text=unicodedata.normalize('NFKC',text).casefold()
    text=re.sub(r'[\u064b-\u065f\u0670\u0591-\u05c7]','',text)
    return text.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا'}))


def _matches(pattern,text):
    return list(re.finditer(r'(?<!\w)(?:'+pattern+r')(?!\w)',text,re.I))


def _negated(text,position):
    prefix=re.split(r'[;,،؛.!?\n]|\b(?:but|instead|אלא)\b|لكن|بل ',text[:position])[-1]
    prefix=re.sub(r'\bnot\s+only\b','',prefix)
    return bool(re.search(r"(?:\b(?:not|never|without|no|אל|בלי)\b|\bو?(?:لا|ما|بدون|دون)\b|don't|don’t)\s+(?:\S+\s+){0,7}$",prefix))


def is_continuation(text):
    """Whole-request continuation only; extra actions require a new checked task."""
    value=_normalized(text.strip()).strip('.!؟? ')
    return bool(re.fullmatch(r'(?:من فضلك\s+)?(?:كمل|اكمل|تابع)(?:\s+من محل ما وقفنا)?|(?:please\s+)?(?:continue|resume)(?:\s+please)?|המשך(?:\s+בבקשה)?',value))


def _status_request(text):
    return bool(re.fullmatch(r'(?:وين وصلنا|اين وصلنا|شو صار(?: بالمهمة)?|ما حالة المهمة|what is the status|status(?: update)?|how far did we get|מה הסטטוס)[.!؟?\s]*',text))

def catalog():
    manifest = json.loads((ROOT/'config/skills-batch-01.json').read_text('utf-8'))
    from backend.skill_registry import validate_catalog
    return validate_catalog(manifest)

def classify(text: str, previous: dict | None = None) -> Intent:
    if not isinstance(text,str) or not text.strip() or len(text)>8000:
        raise ValueError('Skill request must contain 1 to 8000 characters')
    clean = text.strip()
    following = is_continuation(clean)
    if following and previous:
        data = dict(previous)
        data['basis'] = 'Explicit continuation of the selected saved task; no new authorization.'
        return Intent.model_validate(data)
    normalized=_normalized(clean)
    if _status_request(normalized):
        return Intent(intent=['status'],goal=clean,constraints=['read-only'],requestedOutputs=[],references=[],
                      ambiguities=[] if previous else ['حدد المهمة التي تريد معرفة حالتها.'],
                      basis='Status inquiry only; the selected saved task is not cancelled or re-executed.')
    references=list(dict.fromkeys(m.group(0) for m in REFERENCE.finditer(clean)))
    # Filenames, code and quoted content are data, not workflow instructions.
    command=REFERENCE.sub(' ',clean)
    command=re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”',' ',command)
    command=_normalized(command)
    active={name:[m for m in _matches(pattern,command) if not _negated(command,m.start())] for name,pattern in RULES}
    # A test mentioned as the subject of explanation/review is not a command to run it.
    if active['test'] and (active['explain'] or active['review']) and not (active['modify'] or active['create']):
        active['test']=[m for m in active['test'] if m.group(0)!='test']
    intents=[name for name,_ in RULES if active[name]]
    outputs=[]
    for name,pattern in OUTPUTS:
        found=[m for m in _matches(pattern,command) if not _negated(command,m.start())]
        if found and (active['create'] or active['modify']): outputs.append(name)
        elif active['modify'] and any(x.lower().endswith('.'+name) for x in references): outputs.append(name)
        elif active['create']:
            # Explicit output filename; an input after "from/of" is not an output.
            for ref in references:
                before=_normalized(clean[:clean.find(ref)])
                if ref.lower().endswith('.'+name) and not re.search(r'\b(?:from|of)\s+|(?:من|מתוך)\s+',before):
                    outputs.append(name);break
    if 'translate' in intents: outputs.append('translation')
    ambiguities = ['حدد المهمة السابقة للمتابعة.'] if following else []
    if not intents and not outputs:
        intents = ['explain']
        ambiguities.append('التوجيه اللغوي محدود؛ اختر المهارة إذا لم يطابق قصدك.')
    constraints = []
    if re.search(r'محلي|\blocal(?:ly)?[ -]+only\b|מקומי', command, re.I): constraints.append('local-only')
    if re.search(r'(?:دون|بدون|من غير)\s+تعديل|(?:لا|ما)\s+(?:تعدل|تصلح|تغير)(?:ه|ها)?|read[ -]only|(?:do not|don.t|without|never)\s+(?:edit|modify|fix(?:ing)?|change)|no\s+changes|בלי\s+שינויים|אל\s+(?:תשנה|תתקן)',command,re.I):
        constraints.append('read-only')
        intents = [x for x in intents if x not in {'modify','create'}]
        outputs=[x for x in outputs if x=='translation']
    return Intent(intent=intents or ['explain'], goal=clean, constraints=constraints,
                  requestedOutputs=outputs, references=references,
                  ambiguities=ambiguities, basis='Bounded Arabic/Hebrew/English rules; explicit selections take priority. Not a universal language classifier.')

def select_skills(intent: Intent, explicit: list[str] = (), document_ids=()):
    manifest=catalog()
    explicit=[manifest.get('aliases',{}).get(x,x) for x in explicit]
    known = {x['slug'] for x in manifest['skills']}
    if any(x not in known for x in explicit): raise ValueError('Unknown skill')
    selected = list(dict.fromkeys(explicit))
    if not selected and ('status' in intent.intent or is_continuation(intent.goal)):
        selected.append('intent-routing')  # Attachment selection is not a new action.
    if not selected:
        from backend.skill_system import route as system_route
        selected=system_route(intent.goal)
    if not selected:
        from backend.skill_batch03 import route
        selected=route(intent.goal)
    if not selected:
        from backend.skill_batch04 import route
        selected=route(intent.goal)
    if not selected:
        goal=_normalized(intent.goal)
        explicit_web=bool(_matches(r'web|internet|online|الويب|الانترنت|באינטרנט|ברשת|אינטרנט',goal))
        pdf_mentioned=any(x.lower().endswith('.pdf') for x in intent.references) or bool(re.search(r'\bPDF\b',intent.goal,re.I))
        full_read='summarize' in intent.intent or bool(_matches(r'read|اقرا|قراءة|קרא',goal)) or pdf_mentioned
        scoped_lookup=bool(document_ids and not intent.requestedOutputs and 'translate' not in intent.intent and ('research' in intent.intent or not full_read))
        if scoped_lookup: selected.append('hybrid-retrieval')
        elif document_ids or pdf_mentioned: selected.append('pdf-reading')
        if 'research' in intent.intent and (not document_ids or explicit_web): selected.append('web-research')
        if 'translate' in intent.intent: selected.append('translation')
        if 'summarize' in intent.intent: selected.append('text-summarization')
        actions=_normalized(re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”',' ',intent.goal))
        if any(not _negated(actions,m.start()) for m in _matches(r'text to speech|read aloud|و?(?:ت?اقرا|تقرا)(?:ه|ها)? بصوت|و?حول(?:ه|ها)? (?:الى|ل)صوت|قراءة صوتية|ו?(?:קרא|קריאה) בקול',actions)):selected.append('text-to-speech')
        if any(not _negated(actions,m.start()) for m in _matches(r'find file|find the file|و?ابحث عن الملف|و?ابحث عن ملف|ו?חפש קובץ',actions)):selected=['file-search']
        for out, slug in [('xlsx','spreadsheet-creation'),('docx','docx-authoring'),('pptx','slide-authoring')]:
            if out in intent.requestedOutputs and not ('explain' in intent.intent and not {'create','modify','translate'}.intersection(intent.intent)): selected.append(slug)
        office_target=bool({'docx','xlsx','pptx'}.intersection(intent.requestedOutputs)) or bool(re.search(r'\b(?:Word|Excel|PowerPoint)\b|مستند|מסמך',intent.goal,re.I)) or any(x.lower().endswith(('.docx','.xlsx','.pptx')) for x in intent.references)
        if 'modify' in intent.intent and not office_target: selected.append('code-generation')
        elif 'review' in intent.intent and not office_target: selected.append('code-review')
        elif 'debug' in intent.intent: selected.append('root-cause-debugging')
        if 'test' in intent.intent: selected.append('end-to-end-testing')
        if document_ids and not intent.requestedOutputs and 'pdf-reading' not in selected: selected.append('hybrid-retrieval')
        if not selected: selected.append('intent-routing')
    if 'read-only' in intent.constraints and WRITING_SKILLS.intersection(selected):
        raise ValueError('Read-only task cannot select a writing operation')
    if document_ids and any(x in selected for x in ('translation','docx-authoring','slide-authoring','text-summarization','text-to-speech')) and not {'pdf-reading','file-content-reading'}.intersection(selected):
        selected.insert(0,'pdf-reading')
    if len(selected) > MAX_ACTIVE: raise ValueError('Active skill budget exceeded')
    order={'pdf-reading':10,'ocr-extraction':11,'hybrid-retrieval':12,'web-research':13,'fact-checking':15,'citation-grounding':16,
           'file-search':8,'file-content-reading':10,'document-ingestion':11,'retrieval-permission-filter':12,
           'primary-source-search':17,'freshness-check':18,'translation':20,'text-summarization':21,
           'spreadsheet-creation':30,'docx-authoring':31,'slide-authoring':32,'text-to-speech':40,
           'artifact-delivery':90,'completion-review':98,'tool-result-verification':99}
    from backend.skill_registry import resolve_dependencies
    ordered=sorted(dict.fromkeys(selected),key=lambda slug:order.get(slug,0))
    return resolve_dependencies(ordered,manifest,strict=True)['order']

def load_instructions(selected,pin=None):
    from backend.skill_loading import load_packages
    from backend.skill_system import validate_activation
    validate_activation(selected,catalog())
    return load_packages(selected,catalog(),ROOT,MAX_INSTRUCTION_CHARS,pin=pin)['text']


def plan(intent, selected, typed_outputs=None):
    """Stable IDs and actual data dependencies; order is not permission to execute."""
    steps = [];selected=list(dict.fromkeys(selected))
    titles={x['slug']:x['display_name'] for x in catalog()['skills']}
    if len(selected)>MAX_ACTIVE or any(x not in titles for x in selected):
        raise ValueError('Invalid bounded skill plan')
    for slug in selected:
        dependencies=[]
        if slug=='ocr-extraction' and 'pdf-reading' in selected: dependencies=['pdf-reading']
        elif slug in {'hybrid-retrieval','translation'}:
            dependencies=[x for x in ('pdf-reading','ocr-extraction') if x in selected][-1:]
            if slug=='translation' and 'web-research' in selected: dependencies.append('web-research')
        elif slug in {'fact-checking','citation-grounding'}:
            dependencies=[x for x in ('hybrid-retrieval','web-research') if x in selected]
            if not dependencies: dependencies=[x for x in ('pdf-reading','ocr-extraction') if x in selected][-1:]
        elif slug=='spreadsheet-creation' and 'xlsx' in (typed_outputs or ()):
            dependencies=[]  # Explicit typed table is not produced by text/OCR/translation.
        elif slug in {'spreadsheet-creation','docx-authoring','slide-authoring'}:
            dependencies=[x for x in ('pdf-reading','ocr-extraction','translation') if x in selected][-1:]
            if slug in {'docx-authoring','slide-authoring'} and 'web-research' in selected:
                dependencies.append('web-research')
        elif slug=='end-to-end-testing' and 'code-generation' in selected: dependencies=['code-generation']
        elif slug=='tool-result-verification': dependencies=[x for x in selected if x not in {'tool-result-verification','intent-routing','skill-routing'}]
        if slug in {'text-summarization','text-to-speech','primary-source-search','freshness-check'}:
            dependencies=[x for x in ('pdf-reading','file-content-reading','web-research','translation') if x in selected][-1:]
            if slug=='text-to-speech' and 'text-summarization' in selected:dependencies=['text-summarization']
        if slug in {'docx-authoring','slide-authoring'} and 'text-summarization' in selected:dependencies=['text-summarization']
        sid = 'step-'+slug
        steps.append({'id':sid, 'skill':slug, 'title':titles[slug],
                      'status':'pending', 'depends_on':['step-'+x for x in dependencies], 'output_refs':[]})
    return steps

def criteria(intent, selected):
    output = [{'criterionId':'scope','requirement':'Authorized project and unchanged source revisions at delivery', 'verificationMethod':'server ACL and snapshot recheck','evidence':[], 'status':'pending'}]
    for slug in selected:
        goal=_normalized(intent.goal)
        formulas=any(not _negated(goal,m.start()) for m in _matches(r'formulas?|صيغ|معادلات|נוסחאות',goal))
        requirement = {'spreadsheet-creation':'Editable typed cells preserving zero, missing values and input types; '+('native formulas required; target-engine recalculation must be observed' if formulas else 'formulas only where requested; target-engine display requires separate acceptance'),
                       'translation':'Every input segment translated, protected numbers and URLs preserved; linguistic review separate',
                       'docx-authoring':'Editable Word package; page rendering requires separate acceptance',
                       'slide-authoring':'Editable presentation package; slide rendering requires separate acceptance',
                       'code-generation':'Scoped reviewable patch preserves unrelated edits; actual behavior checks and final diff required, build alone is insufficient',
                       'code-review':'Findings identify real source locations, conditions and impact; review does not modify files',
                       'root-cause-debugging':'Reproduction or an explicit blocker distinguishes observed evidence from the proposed cause',
                       'end-to-end-testing':'Real user flow, persisted result and authorized readback; no claim from a success toast alone',
                       'pdf-reading':'Requested source coverage and page evidence; unreadable or unprocessed pages remain explicitly incomplete',
                       'web-research':'Actual permitted source reads with dates or unknown dates and evidence; snippets alone do not pass'}.get(slug,'Requested operation has a real executor result and traceable evidence')
        output.append({'criterionId':slug,'requirement':requirement,'verificationMethod':'executor result and artifact inspection','evidence':[], 'status':'pending'})
    return output

def verify_receipt(receipt, artifact_exists=True):
    """Only call with a receipt produced by the server, never uploaded stdout."""
    if receipt.get('status') in {'queued','running','pending'}: return 'unknown'
    if receipt.get('exit_code',0) != 0 or not artifact_exists: return 'failed'
    if receipt.get('status') in {'failed','cancelled'}: return 'failed'
    if receipt.get('verification') in {'partial','unknown','failed'}: return receipt['verification']
    if receipt.get('status') == 'partial': return 'partial'
    if receipt.get('status') != 'completed' or not receipt.get('evidence'): return 'unknown'
    return 'verified'

def chat_instructions(question):
    """Lazy advisory context; a chat response cannot execute these tools."""
    try:
        intent = classify(question)
        selected = select_skills(intent)
    except ValueError:
        return ''  # Optional workflow guidance must not reject a longer chat.
    if selected == ['intent-routing']:
        return ''
    try: instructions = load_instructions(selected)
    except (OSError, ValueError): return ''  # Chat remains usable without optional packages.
    if len(instructions.encode('utf-8'))>2600:
        return '\nThis task requires a compound workflow. Use the Riwaq skill workbench for actual execution; never claim files or tests were created by this text response.'
    return ('\nRiwaq selected workflow guidance. This does not authorize or execute tools. '
            'If an output requires execution, direct the user to تنفيذ مهارة or its existing workbench; never claim an unexecuted result.\n'+instructions)
