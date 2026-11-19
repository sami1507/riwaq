"""Batch 03 adapters over the existing task, ACL, artifact and isolated worker paths."""
from __future__ import annotations
import hashlib
import json
import re
from fastapi import HTTPException
from backend import skill_files, skill_operations, skill_content
from backend.db import get_db

DOCUMENTS = {'pdf-creation':'pdf-create','pdf-form-filling':'pdf-form-fill','pdf-merge-split':('pdf-merge','pdf-split'),
    'pdf-redaction':'pdf-redact','docx-editing':'docx-edit','document-comparison':'docx-compare',
    'template-population':'template-fill','file-conversion':'convert','document-table-extraction':'table-extract'}
DATA = {'spreadsheet-reading':'read','formula-debugging':'formulas','spreadsheet-cleaning':'clean','csv-import-export':'csv',
    'table-joins':'join','pivot-analysis':'aggregate','spreadsheet-charts':'chart','data-profiling':'profile',
    'exploratory-analysis':'explore','descriptive-statistics':'stats'}
WEB = {'browser-navigation','page-content-extraction','web-form-filling'}
SKILLS = set(DOCUMENTS)|set(DATA)|WEB|{'artifact-versioning','folder-organization','cross-document-synthesis'}


def route(goal):
    """Narrow multilingual proposals; confirmed field mappings still come from the UI."""
    from backend.skill_core import _normalized,_matches,_negated
    text=_normalized(re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”',' ',goal))
    if _matches(r'explain|اشرح|הסבר',text):return []
    rules=[
      ('pdf-creation',r'(?:انش[ئء]|اعمل|create|make|צור)\s+(?:تقرير\s+)?pdf'),
      ('pdf-merge-split',r'(?:ادمج|دمج|قسم|split|merge|מזג|פצל)\s+(?:ملفات\s+)?pdf'),
      ('pdf-form-filling',r'(?:املا|fill|מלא)\s+(?:نموذج\s+|form\s+|טופס\s+)?pdf'),
      ('pdf-redaction',r'(?:نقح|redact|השחר)\s+(?:بيانات\s+)?pdf'),
      ('docx-editing',r'(?:عدل|edit|ערוך)\s+(?:مستند\s+)?(?:word|docx)'),
      ('document-comparison',r'(?:قارن|compare|השווה)\s+(?:نسختي|نسختين|documents|מסמכים)'),
      ('template-population',r'(?:املا|fill|מלא)\s+(?:القالب|قالب|template|תבנית)'),
      ('document-table-extraction',r'(?:استخرج|extract|חלץ)\s+(?:الجداول|جدول|tables?|טבלאות)'),
      ('spreadsheet-reading',r'(?:اقرا|read|קרא)\s+(?:المصنف|مصنف|workbook|גיליון)'),
      ('formula-debugging',r'(?:اصلح|fix|תקן)\s+(?:الصيغة|الصيغ|formula|נוסחה)'),
      ('spreadsheet-cleaning',r'(?:نظف|clean|נקה)\s+(?:الجدول|البيانات|table|data|נתונים)'),
      ('table-joins',r'(?:اربط|join|חבר)\s+(?:الجدولين|الجداول|tables?|טבלאות)'),
      ('data-profiling',r'(?:افحص جودة البيانات|profile data|בדוק איכות נתונים)'),
      ('descriptive-statistics',r'(?:احسب الاحصاء|احسب المتوسط|descriptive statistics|סטטיסטיקה תיאורית)'),
      ('cross-document-synthesis',r'(?:ادمج معرف[ةه] الوثائق|ادمج ادل[ةه] الوثائق|synthesize documents|שלב מסמכים)')]
    selected=[]
    for slug,pattern in rules:
        if any(not _negated(text,m.start()) for m in _matches(pattern,text)):selected.append(slug)
    return selected


def effects(c,rid):
    """Keep committed effect identifiers even if subsequent work fails or is cancelled."""
    output=[]
    for row in c.execute('SELECT id,state FROM skill_folder_operations WHERE run_id=?',(rid,)):
        output.append({'kind':'logical_folder_change','operation_id':row['id'],'state':row['state'],'undo_available':row['state']=='applied'})
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skill_web_submissions'").fetchone():
        for row in c.execute('SELECT operation_id,state FROM skill_web_submissions WHERE operation_id=?',(rid,)):
            output.append({'kind':'web_submission','operation_id':row['operation_id'],'state':row['state'],'verified_effect':False,'automatic_replay':False})
    return output


def requested_operations(inputs):
    selected=[]
    for field,mapping in [('documentOperation',DOCUMENTS),('dataOperation',DATA)]:
        value=getattr(inputs,field,None)
        if value:
            selected.extend(slug for slug,ops in mapping.items() if value['operation'] in (ops if isinstance(ops,tuple) else (ops,)))
    if inputs.folderOperation:selected.append('folder-organization')
    if inputs.versionOperation:selected.append('artifact-versioning')
    return selected


def validate_plan(selected,inputs):
    """No ignored operation or unbound bytes; validation runs before any tool call."""
    from backend.skill_data_revision import validate_plan as validate_data_report
    validate_data_report(selected,inputs)
    for field,mapping in [('documentOperation',DOCUMENTS),('dataOperation',DATA)]:
        value=getattr(inputs,field)
        targets=set(selected)&set(mapping)
        if value:
            matching={s for s,ops in mapping.items() if value['operation'] in (ops if isinstance(ops,tuple) else (ops,))}
            if targets!=matching:raise ValueError('Selected skill and structured operation differ')
        elif targets:raise ValueError('This operation requires confirmed structured inputs')
    if inputs.webOperation and not set(selected)&WEB:raise ValueError('Web operation has no selected executor')
    if len(set(selected)&WEB)>1:raise ValueError('Choose one website operation per task')
    if set(selected)&WEB and not inputs.webOperation:raise ValueError('Website source and operation inputs are required')
    for slug,field in [('artifact-versioning','versionOperation'),('folder-organization','folderOperation')]:
        if bool(getattr(inputs,field))!=(slug in selected):raise ValueError('Operation selection and input differ')
    ids={s.id for s in inputs.sourceRefs}
    if len(ids)!=len(inputs.sourceRefs):raise ValueError('Choose one version per source per task')
    if inputs.documentOperation and set(inputs.documentOperation['sourceIds'])!=ids:raise ValueError('Document source references do not match')
    if inputs.dataOperation:
        source=inputs.dataOperation.get('sourceId')
        if ids!=({source} if source else set()):raise ValueError('Data source references do not match')
    writers=sum(s in DOCUMENTS or s in DATA or s in {'spreadsheet-creation','docx-authoring','slide-authoring','text-to-speech'} for s in selected)
    if inputs.outputVersion and writers!=1:raise ValueError('Updating an existing file requires one producing operation')
    if inputs.outputVersion and inputs.versionOperation:raise ValueError('Restore already specifies its target version')
    if 'cross-document-synthesis' in selected and not 2<=len(inputs.documentIds)<=8:raise ValueError('Synthesis requires two to eight indexed documents')


def output_update(inputs):
    if inputs.outputVersion:return inputs.outputVersion.model_dump()
    op=inputs.versionOperation
    if op and op.action=='restore':return {'artifactId':op.artifactId,'expectedVersion':op.expectedVersion,'reason':'Restore version '+str(op.version)}
    return None


async def run(slug,inputs,who,pid,rid,check):
    check()
    if inputs.dataReport:
        from backend.skill_data_revision import run as run_data_report
        return await run_data_report(inputs,who,pid,rid,check)
    if slug in DOCUMENTS or slug in DATA:
        with get_db() as c:sources=[skill_files.resolve(c,who,pid,r,active=True,writing_run_id=rid) for r in inputs.sourceRefs]
        if slug in DOCUMENTS:
            from backend.skill_documents import run_document_operation
            transformed=await skill_operations.stopped_thread(lambda stop:run_document_operation(inputs.documentOperation,sources,check=check,stop=stop),check)
        else:
            from backend.skill_transform import execute_transform
            from backend.skill_artifacts import build_artifact
            request=inputs.dataOperation
            if request.get('format')=='xlsx' or request['operation']=='chart':
                template=build_artifact('xlsx',{'title':'Data','columns':[{'name':'Value'}],'rows':[['']]})
                sources.append({'file_id':'trusted-template','name':'template.xlsx','version':1,'sha256':template.sha256,'content':template.content,'role':'template'})
            transformed=await skill_operations.stopped_thread(lambda stop:execute_transform('data',request,sources,check=check,stop=stop),check)
        check()
        outputs=[{'name':slug+'-'+x.filename,'mime':x.media_type,'sha256':x.sha256,'content':x.content} for x in transformed.artifacts]
        limitations=[v for x in transformed.artifacts for v in x.limitations]
        result={'operation':slug,'output':transformed.result,'source_refs':list(transformed.source_refs),'execution_receipt':transformed.receipt,
                'artifact_checks':[x.verification for x in transformed.artifacts],'limitations':limitations}
        partial=transformed.result.get('status')=='partial'
        if slug in DATA:
            partial=partial or transformed.result.get('table') is None or bool(transformed.result.get('warnings')) or bool(transformed.result.get('summary',{}).get('formula_debugging',{}).get('after',{}).get('errors'))
        return result,outputs,partial, 'اكتملت الأجزاء المدعومة، وبقيت عناصر تحتاج مراجعة كما هو موضح في النتيجة.' if partial else 'أُنجزت المعالجة المحلية. راجع النتيجة والحدود المسجلة وافتح الملف الناتج إن وجد.'
    if slug in WEB:
        from backend import skill_web
        result=await skill_operations.stopped_thread(lambda stop:skill_web.run(slug,who,pid,inputs.webOperation,stop=stop,check=check,operation_id=rid),check)
        submission=result.get('submission')
        partial=bool(submission and not submission.get('verified_effect')) or bool(result.get('truncated') or result.get('issues'))
        return result,[],partial,result.get('text') or ('استُقبل رد الموقع؛ اكتمال الإجراء غير مثبت.' if partial else 'اكتملت قراءة الصفحة أو تجهيز المسودة ضمن النطاق المسجل.')
    if slug=='folder-organization':
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE');check()
            result=skill_files.organize(c,who,pid,inputs.folderOperation,run_id=rid)
        return result,[],False,'خريطة تنظيم المجلدات جاهزة.' if result['status']=='preview' else 'طُبق تنظيم ملفات المشروع وسُجل مسار الرجوع.'
    if slug=='artifact-versioning':
        op=inputs.versionOperation
        with get_db() as c:
            if op.action=='list':return skill_files.history(c,who,pid,op.artifactId),[],False,'هذه الإصدارات المحفوظة والمتاحة حاليًا.'
            h,v=skill_files.version(c,who,pid,op.artifactId,op.version)
            if h['latest_version']!=op.expectedVersion:raise HTTPException(409,'File changed before restore')
            result={'restored_from':op.version,'expected_version':op.expectedVersion,'artifact_id':op.artifactId,'history_preserved':True}
            item={'name':h['name'],'mime':v['mime'],'sha256':v['sha256'],'content':v['content']}
        return result,[item],False,'حُفظت النسخة المختارة كإصدار جديد مع الإبقاء على التاريخ.'
    if slug=='cross-document-synthesis':
        with get_db() as c:bundle=skill_content.read_files(c,who,pid,inputs.documentIds)
        # Bounded, source-separated evidence. Shared words/numbers are never entity resolution.
        cards=[]
        for chunk,ref in zip(bundle['chunks'],bundle['source_refs']):
            cards.append({'source_id':ref['id'],'file_id':chunk['file_id'],'location':ref.get('locator',ref.get('page')),
                          'text':chunk['text'],'kind':'direct_source_text'})
        result={'evidence_map':cards,'source_refs':bundle['source_refs'],'coverage':bundle['coverage'],
                'inferences':[],'conflicts':'not_semantically_adjudicated','status':'partial',
                'limitations':['Source-separated evidence assembly only. Conflicts, entity equivalence and cross-document conclusions require review.']}
        text='\n\n'.join('['+x['source_id']+'] '+x['text'] for x in cards)
        return result,[],True,text
    raise ValueError('No Batch 03 executor')
