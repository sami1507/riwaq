"""Presentation-only revisions of a saved task, using existing task/translation engines.

The client supplies a version expectation, never content, an ACL decision or a receipt.
All upstream obligations remain on the existing previousRunId lineage.
"""
import hashlib
import json
from fastapi import HTTPException
from backend import guard

ANALYSIS={'data-profiling','descriptive-statistics','exploratory-analysis','pivot-analysis','table-joins'}


def analysis_narrative(analysis,language):
    """Localized presentation of already-computed statistics, never a recalculation."""
    introduction={'en':'Saved analysis. The original results and files are retained. Technical field names and values are preserved.',
                  'ar':'التحليل المحفوظ. احتُفظ بالنتائج والملفات الأصلية وبأسماء الحقول التقنية وقيمها.',
                  'he':'ניתוח שמור. התוצאות והקבצים המקוריים נשמרו. שמות השדות הטכניים והערכים נשמרו.'}[language]
    labels={'en':{'numeric_count':'known values','missing':'missing','sum':'total','mean':'mean','median':'median'},
            'ar':{'numeric_count':'قيم معروفة','missing':'مفقود','sum':'المجموع','mean':'المتوسط','median':'الوسيط'},
            'he':{'numeric_count':'ערכים ידועים','missing':'חסרים','sum':'סכום','mean':'ממוצע','median':'חציון'}}[language]
    paragraphs=[introduction]
    for value in analysis.values():
        stats=value.get('output',{}).get('summary',{}).get('statistics',{})
        for column,fields in stats.items():
            paragraphs.append(column+': '+'; '.join(label+': '+str(fields[key]) for key,label in labels.items() if key in fields))
    return '\n\n'.join(paragraphs)


def analysis_tables(result,language):
    """Render stored values without recomputing or translating identifiers/quantities."""
    from backend.skill_artifacts import Table
    headings={'en':['Field','Saved value'],'ar':['الحقل','القيمة المحفوظة'],'he':['שדה','ערך שמור']}[language]
    tables=[]
    def cell(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    for name,record in result.get('structured',{}).items():
        if name not in ANALYSIS:continue
        output=record.get('output',{})
        summary=output.get('summary',{})
        rows=[[key,cell(value)] for key,value in summary.items() if key not in {'version','operation'}]
        if rows:tables.append(Table(headers=headings,rows=rows).model_dump())
        table=output.get('table')
        if table and table.get('rows'):
            tables.append(Table(headers=table['columns'],rows=[[cell(v) for v in row] for row in table['rows']]).model_dump())
    if len(tables)>20:raise ValueError('Saved analysis exceeds report table limits')
    return tables


def result_hash(row):
    return guard.digest({'state':row['state'],'result':json.loads(row['document']).get('result',{})})


def availability(row):
    result=json.loads(row['document']).get('result',{})
    text=result.get('text','')
    possible=row['state'] in {'completed','partial'} and isinstance(text,str) and bool(text.strip()) and len(text)<=16000
    return {'available':possible,'result_hash':result_hash(row),'output_kind':'docx',
            'scope':'Saved narrative only; analysis and original files are retained. Layout is regenerated, not edited in place.'}


def saved_analysis(c,parent):
    """Follow existing authorized lineage, not a caller-supplied analysis ID."""
    current=parent;seen=set();expected=None
    while current:
        if current['id'] in seen or len(seen)>=32:raise HTTPException(409,'Report lineage exceeds its bound.')
        seen.add(current['id']);doc=json.loads(current['document']);result=doc.get('result',{})
        analysis={k:v for k,v in result.get('structured',{}).items() if k in ANALYSIS}
        if analysis:
            if expected is not None and guard.digest(analysis)!=expected:raise HTTPException(409,'Saved analysis changed; report revision was withheld.')
            return analysis,current['id'],set(doc['selected_skills'])<=ANALYSIS
        binding=result.get('presentation_revision')
        if not binding or not binding.get('analysis_keys'):return {},None,False
        if expected is not None and binding.get('analysis_hash')!=expected:raise HTTPException(409,'Analysis lineage binding changed.')
        expected=binding['analysis_hash'];pid=json.loads(current['payload']).get('previousRunId')
        if binding.get('parent_id')!=pid:raise HTTPException(409,'Report parent binding changed.')
        current=c.execute('SELECT * FROM skill_runs WHERE id=? AND owner_id=? AND project_id=?',(pid,parent['owner_id'],parent['project_id'])).fetchone()
        if not current:raise HTTPException(409,'Saved analysis is no longer available.')
    return {},None,False


def check_parent(c,parent,expected):
    if result_hash(parent)!=expected:raise HTTPException(409,'Saved report changed; refresh it before changing its language.')
    if not availability(parent)['available']:raise HTTPException(422,'A completed saved narrative within 16000 characters is required.')
    result=json.loads(parent['document'])['result']
    for artifact in result.get('artifacts',[]):
        saved=c.execute('SELECT content,sha256 FROM skill_artifacts WHERE run_id=? AND name=?',(parent['id'],artifact['name'])).fetchone()
        if not saved or saved['sha256']!=artifact['sha256'] or hashlib.sha256(saved['content']).hexdigest()!=artifact['sha256']:
            raise HTTPException(409,'Saved report artifact is missing or changed; revision was not started.')
    return result


def bind(c,parent,body):
    request=body.inputs.reportRevision
    if request is None:return body,None
    if parent is None:raise HTTPException(422,'Choose the saved report to revise.')
    # No user text, replacement data or effectful operation may hide in this path.
    others=body.inputs.model_dump(exclude_defaults=True,exclude_none=True)
    if set(others)-{'reportRevision'} or body.requestedSkills:
        raise HTTPException(422,'A report-language revision cannot include replacement input or another operation.')
    result=check_parent(c,parent,request.expectedResultHash)
    analysis,analysis_id,template=saved_analysis(c,parent)
    try:analysis_tables({'structured':analysis},request.targetLanguage)
    except ValueError as exc:raise HTTPException(422,'Saved analysis exceeds editable report limits; no rows were dropped.') from exc
    text=analysis_narrative(analysis,request.targetLanguage) if template else result['text']
    if len(text.split('\n\n'))>20 or any(len(p)>1600 for p in text.split('\n\n')):
        raise HTTPException(422,'Saved narrative exceeds the translation paragraph limit; no text was truncated.')
    binding={'parent_id':parent['id'],'result_hash':request.expectedResultHash,'target_language':request.targetLanguage,
             'analysis_keys':sorted(analysis),'analysis_hash':guard.digest(analysis),
             'analysis_source_run_id':analysis_id,
             'original_artifacts_retained':True,'source':'server_saved_result','scope':'narrative_language_only',
             'presentation_method':'localized_saved_analysis' if template else 'local_model_translation'}
    effective=body.model_copy(update={'inputs':body.inputs.model_copy(update={'text':text,'targetLanguage':request.targetLanguage}),
                                      'requestedSkills':['docx-authoring'] if template else ['translation','docx-authoring']})
    return effective,binding


def check_link(c,child,parent):
    from backend.skill_data_revision import check_link as check_data_link
    check_data_link(c,child,parent)
    spec=json.loads(child['payload']).get('inputs',{}).get('reportRevision')
    if spec:check_parent(c,parent,spec['expectedResultHash'])
