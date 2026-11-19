"""Bounded request understanding; observations never grant resource authority.

The original request is retained. Matching normalization is not applied to file
identities, code, evidence or output. This is not a general semantic classifier.
"""
import re
import unicodedata

VERSION='request-understanding-1'
DIGITS=str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789')

def fold(text):
    text=unicodedata.normalize('NFKC',text).casefold().translate(DIGITS)
    text=re.sub(r'[\u064b-\u065f\u0670]','',text)
    return text.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا'}))

def command_text(text):
    # Code and source quotations are data even when they contain action verbs.
    return fold(re.sub(r'```[\s\S]*?```|`[^`]*`|"[^"\n]*"|“[^”\n]*”|«[^»]*»',' ',text))

def _positive(pattern,text):
    from backend.skill_core import _negated
    return any(not _negated(text,m.start()) for m in re.finditer(pattern,text,re.I))

def analyze(text,resources=(),*,ordered=False,has_previous=False):
    if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ValueError('Invalid request size')
    if not isinstance(resources,(list,tuple)) or len(resources)>16:raise ValueError('Invalid resource scope')
    command=command_text(text)
    output={'version':VERSION,'original_request':text,'deliverables':[],'constraints':{},
            'resolved_resource':None,'clarifications':[],'assumptions':[],
            'scope':'bounded request rules; captured requirements are not verified output quality'}
    patterns={
      'analysis':r'(?<!\w)(?:و?حلل|تحليل|analy[sz]e|analysis|נתח)(?!\w)',
      'summary':r'(?<!\w)(?:و?لخص(?:ه|ها)?|تلخيص|ملخص(?:ا)?|summary|summari[sz]e|סכם)(?!\w)',
      'chart':r'(?<!\w)(?:رسم(?:ا)?|رسما? بيانيا|chart|graph|תרשים)(?!\w)',
      'file':r'(?<!\w)(?:و?ملف نتائج|و?ملفا? قابل(?:ا)? للتنزيل|results? file|downloadable file)(?!\w)',
    }
    output['deliverables']=[name for name,pattern in patterns.items() if _positive(pattern,command)]
    creating=_positive(r'(?<!\w)(?:و?(?:انشئ|اكتب|ارسم|اعمل|جهز)|create|write|make|generate|draw|צור)(?!\w)',command)
    # A referenced chart/file is not a request to generate another output.
    # These bounded rules deliberately leave implicit paraphrases unclassified.
    if not creating:
        output['deliverables']=[k for k in output['deliverables'] if
            k=='analysis' and _positive(r'(?<!\w)(?:و?حلل|analy[sz]e|נתח)(?!\w)',command) or
            k=='summary' and _positive(r'(?<!\w)(?:و?لخص(?:ه|ها)?|summari[sz]e|סכם)(?!\w)',command)]
    languages=[]
    for code,pattern in [('ar',r'بالعربي(?:ة)?|in arabic|בערבית'),('en',r'بالانجليزي(?:ة)?|in english|באנגלית'),('he',r'بالعبري(?:ة)?|in hebrew|בעברית')]:
        if _positive(pattern,command):languages.append(code)
    if len(languages)==1:output['constraints']['language']=languages[0]
    elif len(languages)>1:output['assumptions'].append('Multiple output languages named; language-to-output mapping needs review.')
    if re.search(r'(?:لا|بدون|دون)\s+(?:تذكر\s+)?الاسعار|(?:do not mention|exclude|without)\s+prices',command):output['constraints']['exclude_prices']=True
    if re.search(r'احتفظ\s+باسماء|preserve\s+(?:product\s+)?names|keep\s+(?:product\s+)?names',command):output['constraints']['preserve_names']=True
    meta_only=bool(re.match(r'\s*(?:اشرح\s+(?:العبارة|لماذا|التناقض)|فسر\s+(?:العبارة|التناقض)|ما معنى|explain\s+(?:why|the phrase|the contradiction)|what does)(?!\w)',command)) and not re.search(r'واكتب|ثم اكتب|and write|then write',command)
    count_command='' if meta_only else command
    exact=re.findall(r'(\d+)\s*(?:كلم[ةه]|words?)\s*(?:بالضبط|exactly)|(?:exactly)\s+(\d+)\s+words?',count_command)
    maximum=re.findall(r'(?:بحد\s+اقصى|حد\s+اقصى|at most|maximum(?: of)?|no more than)\s*(\d+)\s*(?:كلم[ةه]|words?)',count_command)
    exact_values=[int(a or b) for a,b in exact]
    if exact_values:output['constraints']['exact_words']=exact_values[-1]
    if maximum:output['constraints']['max_words']=min(map(int,maximum))
    if len(set(exact_values))>1 or exact_values and maximum and exact_values[-1]>min(map(int,maximum)):
        output['clarifications'].append({'code':'conflicting_word_limits','message':'طلبت عدد كلمات محددًا يتجاوز الحد الأقصى؛ أي القيدين تريد اعتماده؟'})
    bullet=re.search(r'(?:ب|in\s+)?(\d+|خمس|خمسة|ثلاث|ثلاثة|اربع|اربعة|ست|ستة)\s*(?:نقاط|نقط|bullets?)',command)
    if bullet:
        value=bullet.group(1);count=int(value) if value.isdecimal() else {'خمس':5,'خمسة':5,'ثلاث':3,'ثلاثة':3,'اربع':4,'اربعة':4,'ست':6,'ستة':6}[value]
        output['constraints']['bullets']=count
        if not 1<=count<=8:output['clarifications'].append({'code':'summary_bullet_limit','message':'مسار التلخيص يدعم من نقطة إلى ثماني نقاط؛ اختر نطاقًا مناسبًا.'})
    destructive=_positive(r'(?<!\w)(?:احذف|امسح|delete|remove)(?!\w)',command)
    referential=bool(re.search(r'هذا|هذ[اه]|الثاني|\b(?:this|it|second)\b|لخصه|عدله',command))
    second=bool(re.search(r'الملف\s+الثاني|\bsecond\s+file\b',command))
    if second:
        if ordered and len(resources)>=2:output['resolved_resource']=resources[1]
        else:output['clarifications'].append({'code':'ordered_resource_required','message':'حدّد ترتيب الملفات أو اختر الملف المقصود بالثاني.'})
    elif referential and len(resources)==1:output['resolved_resource']=resources[0]
    elif destructive and (referential or not resources):
        output['clarifications'].append({'code':'ambiguous_delete_target','message':'حدّد الملف المطلوب حذفه؛ لم يُنفذ أي حذف.'})
    elif referential and len(resources)>1 and not has_previous:
        output['clarifications'].append({'code':'ambiguous_resource','message':'أي ملف تقصد؟ توجد عدة ملفات مختارة.'})
    return output

def assess_output(record,document,artifact_checks):
    """Check observable coverage only. No self-rated semantic confidence."""
    result=document.get('result',{});steps={s['skill']:s['status'] for s in document.get('steps',[])}
    artifacts=result.get('artifacts',[])
    saved=bool(artifacts) and all(artifact_checks.get(a['name']) is True for a in artifacts)
    analysis_keys={'data-profiling','descriptive-statistics','exploratory-analysis','pivot-analysis','table-joins'}
    coverage={
      'analysis':any(steps.get(k)=='completed' and k in result.get('structured',{}) for k in analysis_keys),
      'summary':bool(result.get('summary',{}).get('bullets')) and steps.get('text-summarization')=='completed',
      'chart':steps.get('spreadsheet-charts')=='completed' and saved,
      'file':saved,
    }
    from backend.skill_data_revision import verified_coverage
    if saved and verified_coverage(result,steps,artifact_checks):
        coverage.update(analysis=True,summary=True,chart=True)
    gaps=[{'kind':'requested_output','id':k,'state':'not_proven'} for k in record.get('deliverables',[]) if not coverage[k]]
    constraints=record.get('constraints',{})
    if constraints.get('bullets') is not None:
        count=len(result.get('summary',{}).get('bullets',[]))
        if count!=constraints['bullets']:gaps.append({'kind':'constraint','id':'bullets','state':'count_mismatch','expected':constraints['bullets'],'actual':count})
    if any(k in constraints for k in ('exact_words','max_words')):
        if artifacts or 'file' in record.get('deliverables',[]):
            # The conversation text may differ from a separately supplied file
            # payload. A byte hash proves integrity, never its word count.
            gaps.append({'kind':'constraint','id':'word_count','state':'not_verified_in_saved_file'})
        else:
            count=len(re.findall(r'\S+',result.get('text','')))
            if 'exact_words' in constraints and count!=constraints['exact_words'] or 'max_words' in constraints and count>constraints['max_words']:
                gaps.append({'kind':'constraint','id':'word_count','state':'count_mismatch','actual':count})
    return {'checked_deliverables':{k:coverage[k] for k in record.get('deliverables',[])},'gaps':gaps,
            'unverified_constraints':[k for k in constraints if k not in {'bullets','exact_words','max_words'}],
            'semantic_quality':'not_assessed','method':'saved resource hashes, step receipts and explicit counts'}
