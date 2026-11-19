"""Bounded math request mapping and result boundary; no model or dynamic code.

The original request remains separate from the normalized operation. Solvers run
only in the existing limited fixed worker. This module also runs in the parent.
"""
from __future__ import annotations
import hashlib
import json
import re

VERSION = 'math-v1'
OPERATIONS = frozenset('batch evaluate solve inequality system matrix_inverse eigen diophantine optimize differentiate derivative_at integrate limit ode_linear root verify_root continuity_root plot exact round percent_changes unit_sum entailment integer_implication risk_change quantifier_order existential_import polynomial_even prime_counterexample ordering bayes dice_sum combinations binomial weighted_mean statistics probability rectangle speed legacy_problem'.split())
MAX_INPUT = 12000
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫−×÷', '01234567890123456789.-*/')


def capability():
    import sys
    from importlib.metadata import version,PackageNotFoundError
    found={}
    for name in ('sympy','mpmath'):
        try:found[name]=version(name)
        except PackageNotFoundError:found[name]=None
    from backend.packaged_workers import bundled
    available=(not getattr(sys,'frozen',False) or bundled('checker',sys)) and found=={'sympy':'1.14.0','mpmath':'1.3.0'}
    return {'available':available,'engines':found,'processing':'local','probe_executed':False,
            'reason':'Installed engine versions match; individual operation limits and worker checks still apply.' if available else 'Requires a fixed checker runtime with SymPy 1.14.0 and mpmath 1.3.0; no model fallback.',
            'operations':sorted(OPERATIONS-{'legacy_problem','batch'}),'version':VERSION}


def bounded(value, depth=0):
    if depth > 12:
        raise ValueError('math_structure_depth_limit')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > 256: raise ValueError('math_integer_limit')
    elif type(value) is str:
        if len(value) > 4096 or any(ord(c) < 32 and c not in '\n\t\r' for c in value):
            raise ValueError('math_string_limit')
    elif type(value) is list:
        if len(value) > 128: raise ValueError('math_array_limit')
        for item in value: bounded(item, depth+1)
    elif type(value) is dict:
        if len(value) > 40 or any(type(k) is not str or len(k)>80 for k in value):
            raise ValueError('math_object_limit')
        for item in value.values(): bounded(item, depth+1)
    else:
        raise ValueError('math_exact_numbers_must_be_strings_or_integers')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('math_duplicate_field')
        result[key] = value
    return result


def natural_symbolic(text):
    """A finite command grammar. It never drops unmatched text or conditions."""
    patterns=[
        (r'(?:حل|solve)\s+(.+?)\s+(?:مع شرح سبب كل خطوة|explaining each step)',
         lambda m:{'operation':'solve','expression':m[1],'explanation_level':'detailed'}),
        (r'(?:ارسم|plot)\s+(.+?)\s+(?:على|on)\s+\[([^,]+),([^\]]+)\]',
         lambda m:{'operation':'plot','expression':m[1],'bounds':[m[2],m[3]]}),
        (r'ارسم\s+(.+?)\s+واشرح سلوكها قرب الصفر',
         lambda m:{'operation':'plot','expression':m[1],'bounds':['-10','10']}),
        (r"(?:حل|solve)\s+y'\s*=\s*y\s+(?:مع|with)\s+y\(([^()]+)\)\s*=\s*([^\s]+)",
         lambda m:{'operation':'ode_linear','coefficient':'1','initial':{'point':m[1],'value':m[2]}}),
        (r'(?:هل|is)\s+(.+?)\s+(?:قابلة للاشتقاق عند|differentiable at)\s+([^\s]+)',
         lambda m:{'operation':'derivative_at','expression':m[1],'point':m[2]}),
        (r'(?:التكامل غير الصحيح|تكامل|integrate)\s+(.+?)\s+(?:من|from)\s+([^\s]+)\s+(?:إلى|to)\s+([^\s]+)',
         lambda m:{'operation':'integrate','expression':m[1],'bounds':[m[2],m[3]]}),
        (r'(?:نهاية|limit)\s+(.+?)\s+(?:عند|at)\s+([^\s]+)',
         lambda m:{'operation':'limit','expression':m[1],'point':m[2]}),
        (r"(?:حل|solve)\s+y'\s*=\s*([+\-\d./]+)\s*\*\s*y\s+(?:مع|with)\s+y\(([^()]+)\)\s*=\s*([^\s]+)",
         lambda m:{'operation':'ode_linear','coefficient':m[1],'initial':{'point':m[2],'value':m[3]}}),
        (r'(?:حل|solve)\s+(.+?)=0\s+(?:على|on)\s+\[([^,]+),([^\]]+)\]\s+(?:بخطأ|with error)\s+x\s*<=\s*([^\s]+)',
         lambda m:{'operation':'root','expression':m[1],'bounds':[m[2],m[3]],'tolerance':m[4]}),
        (r'(?:هل تغير إشارة)\s+(.+?)\s+بين\s+([^\s]+)\s+و([^\s]+)\s+يثبت جذرا',
         lambda m:{'operation':'continuity_root','expression':m[1],'bounds':[m[2],m[3]]}),
        (r'اختبر\s+x=([^\s]+)\s+للدالة\s+(.+?)\s+مع خطأ\s+x<=([^\s]+)',
         lambda m:{'operation':'verify_root','expression':m[2],'point':m[1],'tolerance':m[3]}),
    ]
    for pattern,build in patterns:
        match=re.fullmatch(pattern,text,re.I)
        if match:return build(match)
    match=re.fullmatch(r'(?:احسب|evaluate)\s+(.+?)\s+(?:و|and)\s*(.+)',text,re.I)
    if match:return {'operation':'batch','requests':[{'operation':'evaluate','expression':match[1]},{'operation':'evaluate','expression':match[2]}]}
    match=re.fullmatch(r'(?:حل|solve)\s+(.+?)\s+(?:على الحقيقيات ثم المركبة|over real then complex)',text,re.I)
    if match:return {'operation':'batch','requests':[{'operation':'solve','expression':match[1],'domain':d} for d in ('real','complex')]}
    return None


def original_symbolic_wording(text):
    """Whole mathematical sentence forms; captured quantities are never answers.

    Rewrites preserve the explicit domain, constraints, precision and required
    outputs. An unmatched qualifier is rejected instead of discarded.
    """
    number=r'[+\-]?(?:\d+(?:\.\d+)?|\.\d+)'
    def parameter_statement(match):
        names=set(re.findall(r'[A-Za-z][A-Za-z0-9]*',match[1]))
        parameters={match[2],match[3]}
        targets=names-parameters
        if len(parameters)==2 and parameters<=names and len(targets)==1:
            return 'solve '+match[1]+'; domain: real; variable: '+next(iter(targets))
        return None
    formulas=[
        (r'احسب\s+(.+?)\s+(?:بوصفهما قيمتين عشريتين دقيقتين|بوصفهما عددين عشريين محددين|في صورة كسر مبسط)',lambda m:'evaluate '+m[1]),
        (rf'قيمة\s+({number})\s+زادت\s+({number})%\s+ثم انخفضت\s+({number})%\s+من (?:القيمة|قيمتها) الجديدة',lambda m:json.dumps({'operation':'percent_changes','value':m[1],'changes':[m[2],'-'+m[3]]})),
        (r'احسب\s+(.+?)\s+وفق ترتيب العمليات الرياضي المعتاد',lambda m:'احسب '+m[1]),
        (r'حل\s+«([^»]+)»\s+على الأعداد (الحقيقية|الصحيحة|المركبة)',lambda m:'حل '+m[1]+' على الأعداد '+m[2]),
        (r'ورد التعبير\s+([^\s]+)\s+دون أقواس أو اصطلاح يحدد التجميع',lambda m:m[1]),
        (r'حل\s+(.+?)\s+مرة (?:على|في) الأعداد الحقيقية ومرة (?:على|في) المركبة',lambda m:'حل '+m[1]+' على الحقيقيات ثم المركبة'),
        (r'حل\s+(.+?)\s+دون حصر ([A-Za-z]) في فترة، والزاوية بالراديان',lambda m:'solve '+m[1]+'; domain: real; variable: '+m[2]),
        (r'حل\s+(.+?)\s+بحسب قيم ([A-Za-z])\s+و([A-Za-z])',parameter_statement),
        (r'أوجد جميع حلول\s+(.+?)\s+على الأعداد الحقيقية، والزوايا بالراديان',lambda m:'حل '+m[1]+'; domain: real'),
        (r'حل\s+(.+?)\s+مع القيد\s+(.+)',lambda m:'حل '+m[1]+'; where: '+m[2]),
        (r'أوجد معكوس المصفوفة\s+(\[.+\])',lambda m:'inverse '+m[1]),
        (r'أوجد القيم والمتجهات الذاتية للمصفوفة\s+(\[.+\])',lambda m:'eigen '+m[1]),
        (r'أوجد جميع الحلول الصحيحة للمعادلة\s+(.+)',lambda m:'diophantine '+m[1]+'; domain: integer'),
        (r'أوجد الحد الأدنى العالمي لـ(.+?)\s+على الأعداد الحقيقية',lambda m:'minimize '+m[1]+'; domain: real'),
        (r'اشتق\s+(.+?)\s+بالنسبة إلى\s+([A-Za-z]|س|ص)',lambda m:'differentiate '+m[1]+'; variable: '+m[2]),
        (r'هل (?:الدالة\s+)?(.+?)\s+قابلة للاشتقاق عند\s+([A-Za-z]|س|ص)=([^\s]+)',lambda m:'derivative-at '+m[1]+'; variable: '+m[2]+'; point: '+m[3]),
        (r'احسب التكامل المحدد لـ(.+?)\s+من\s+([^\s]+)\s+إلى\s+([^\s]+)',lambda m:'integrate '+m[1]+'; bounds: '+m[2]+','+m[3]),
        (r'أوجد تكامل\s+(.+?)\s+بالنسبة إلى\s+([A-Za-z]|س|ص)\s+على فترات لا تشمل الصفر',lambda m:'integrate '+m[1]+'; variable: '+m[2]+'; where: '+m[2]+'!=0'),
        (r'احسب نهاية\s+(.+?)\s+عندما\s+([A-Za-z]|س|ص)\s+يقترب من\s+(صفر|[^\s]+)، والزوايا بالراديان',lambda m:'limit '+m[1]+'; variable: '+m[2]+'; point: '+('0' if m[3]=='صفر' else m[3])),
        (r'احسب التكامل غير الصحيح المعتاد لـ(.+?)\s+من\s+([^\s]+)\s+إلى\s+([^\s]+)',lambda m:'integrate '+m[1]+'; bounds: '+m[2]+','+m[3]),
        (r'قيّم التكامل غير الصحيح للدالة\s+(.+?)\s+من\s+([^\s]+)\s+إلى\s+([^\s]+)، ثم اسأل عن القيمة الرئيسية',lambda m:'integrate '+m[1]+'; bounds: '+m[2]+','+m[3]),
        (r'ابحث عن أصغر قيمة لـf\(([A-Za-z])\)=(.+?)\s+على\s+([^<>]+)<\1<([^<>]+)',lambda m:'minimize '+m[2]+'; variable: '+m[1]+'; constraints: '+m[1]+'>'+m[3]+'|'+m[1]+'<'+m[4]),
        (r"حل\s+(y'.+?)\s+مع الشرط\s+(y\(.+)",lambda m:'حل '+m[1]+' مع '+m[2]),
        (r'حل\s+(.+?)\s+على\s+(\[[^\]]+\])\s+بخطأ مطلق في x لا يتجاوز\s+([^\s]+)',lambda m:'حل '+m[1]+' على '+m[2]+' بخطأ x<='+m[3]),
        (r'هل تغير إشارة f\(x\)=(.+?)\s+بين\s+([^\s]+)\s+و([^\s]+)\s+يثبت وجود جذر بينهما',lambda m:'هل تغير إشارة '+m[1]+' بين '+m[2]+' و'+m[3]+' يثبت جذرا'),
        (r'للدالة f\(x\)=(.+?)، اختبر المرشح x=([^\s]+)\s+ومتطلب خطأ في الجذر\s*<=\s*([^\s]+)',lambda m:'اختبر x='+m[2]+' للدالة '+m[1]+' مع خطأ x<='+m[3]),
        (r'لدالة f\(x\)=(.+?)، اختبر x=([^\s]+) كمرشح لجذرها',lambda m:json.dumps({'operation':'verify_root','expression':m[1],'point':m[2]})),
    ]
    for pattern,build in formulas:
        match=re.fullmatch(pattern,text)
        if match:return build(match)
    # Parameter statements explicitly designate what is *not* the unknown.
    match=re.fullmatch(r'حل\s+(.+?)\s+على الأعداد الحقيقية مع معامل\s+([A-Za-z])\s+غير محدد',text)
    if match:
        names=set(re.findall(r'[A-Za-z]',match[1]))
        if len(names)==2 and match[2] in names:
            target=(names-{match[2]}).pop()
            return 'solve '+match[1]+'; domain: real; variable: '+target
    match=re.fullmatch(r'(?:حل النظام|حل)\s+(.+?)\s+و\s*(.+?)(?:\s+على الأعداد (الحقيقية|المركبة|الصحيحة))?',text)
    if match and '=' in match[1] and '=' in match[2]:
        return 'system '+match[1]+' | '+match[2]+('; domain: '+{'الحقيقية':'real','المركبة':'complex','الصحيحة':'integer'}[match[3]] if match[3] else '')
    match=re.fullmatch(r'(?:عظّم|عظم)\s+(.+?)\s+حيث\s+(.+?)\s+على الحقيقيات',text)
    if match:return 'maximize '+match[1]+'; constraints: '+re.sub(r'\s+و\s*','|',match[2])+'; domain: real'
    match=re.fullmatch(r'(?:عظّم|عظم)\s+([A-Za-z])\s+حيث\s+(.+?)\s+و\1 عدد صحيح غير سالب',text)
    if match:return 'maximize '+match[1]+'; constraints: '+match[2]+'|'+match[1]+'>=0; domain: integer'
    return None


def declared_from_text(request):
    if request['operation']=='batch':
        request['requests']=[declared_from_text(r) for r in request['requests']]
        return request
    if request['operation'] not in {'evaluate','solve','inequality','system','diophantine','optimize','differentiate','derivative_at','integrate','limit','ode_linear','root','verify_root','continuity_root','plot'}:
        return request
    expr=' '.join([request.get('expression',''),*request.get('equations',[]),*request.get('constraints',[])])
    tokens=re.findall(r'[A-Za-z][A-Za-z0-9]*',expr.replace('س','x').replace('ص','y'))
    excluded={'sin','cos','tan','exp','ln','log','sqrt','abs','pi','e','i','I'}
    names=sorted(set(tokens)-excluded,key=lambda v:(v!='x',v!='y',v))
    if names:request.setdefault('variables',names)
    if 'variable' not in request:request['variable']='x' if 'x' in names or not names else names[0]
    if request['operation']=='solve' and request.get('domain')=='integer' and len(names)>1:
        request['operation']='diophantine'
    return request


def parse_math(text):
    if type(text) is not str or not 1 <= len(text.strip()) <= MAX_INPUT:
        raise ValueError('math_input_length')
    original = text
    text = text.strip()
    if text.startswith('{'):
        request = json.loads(text, object_pairs_hook=unique_object,
                             parse_float=lambda _: (_ for _ in ()).throw(ValueError('Use strings for exact decimals')))
        if type(request) is not dict: raise ValueError('math_object_required')
    else:
        # Displayed command notation, not general prose extraction. The domain
        # and settings are explicit, optional semicolon clauses.
        text = text.translate(DIGITS).replace('≤','<=').replace('≥','>=').strip().rstrip('؟?')
        text = re.sub(r'√(?=\s*\()', 'sqrt', text.replace('²','^2').replace('³','^3'))
        if text.endswith('.') and not text.endswith('..'):text=text[:-1].rstrip()
        from backend.reasoning_problem import parse_problem
        try:parse_problem(text)
        except ValueError:pass
        else:return {'operation':'legacy_problem','text':text}
        from backend.math_wording import parse_discrete_text
        discrete=parse_discrete_text(text)
        if discrete:bounded(discrete);return discrete
        canonical=original_symbolic_wording(text)
        if canonical:return parse_math(canonical)
        # Paired absolute-value bars in mathematical expressions only. Boolean
        # grammar has already been handled and system separators stay intact.
        if re.match(r'(?:حل|solve|هل الدالة|differentiate|derivative-at|integrate)\b',text) and not re.match(r'(?:حل النظام|solve system)',text):
            text=re.sub(r'\|([^|]+)\|',r'abs(\1)',text)
        special=natural_symbolic(text)
        if special:
            request=declared_from_text(special);bounded(request);return request
        # Inline constraints remain explicit constraints, not stripped prose.
        constrained=re.fullmatch(r'(.+?)\s+(?:مع|where)\s+(.+)',text,re.I)
        if constrained and re.match(r'(حل|solve|عظّم|عظم|maximize|صغّر|صغر|minimize)\b',text,re.I):
            text=constrained[1]+'; constraints: '+re.sub(r'\s+و\s*|\s+and\s+','|',constrained[2])
        text=re.sub(r'^(عظم|عظّم|maximize)\s+(.+?)\s+الصحيح(?=;|$)',r'\1 \2; domain: integer',text)
        if re.match(r'(حل النظام|solve system|system)\b',text,re.I):
            text=re.sub(r';\s*(?!domain\b|المجال\b|variables\b|المتغيرات\b|where\b|constraints\b)(?=[^;]*=)', ' | ',text)
        clauses = re.split(r'\s*[;؛]\s*', text)
        text, options = clauses[0], {}
        for clause in clauses[1:]:
            if not clause: continue
            match = re.fullmatch(r'(domain|المجال|variable|المتغير|variables|المتغيرات|where|بشرط|constraints|bounds|الحدود|point|النقطة|tolerance|الدقة|sense|الهدف)\s*[:=]\s*(.+)', clause)
            if not match: raise ValueError('math_unrecognized_option')
            key = {'المجال':'domain','المتغير':'variable','المتغيرات':'variables','بشرط':'constraints','where':'constraints','الحدود':'bounds','النقطة':'point','الدقة':'tolerance','الهدف':'sense'}.get(match[1],match[1])
            if key in options: raise ValueError('math_duplicate_option')
            value = match[2].strip()
            if key in ('variables','bounds','constraints'):
                value = [v.strip() for v in re.split(r'[,،|]', value)]
            options[key] = value
        domain_match = re.search(r'\s+(?:على الأعداد |على الاعداد |على |over the |over )(الحقيقية|الحقيقيات|المركبة|الصحيحة|reals?|complex|integers?)$', text, re.I)
        if domain_match:
            if 'domain' in options: raise ValueError('math_duplicate_domain')
            options['domain'] = domain_match[1].lower(); text=text[:domain_match.start()].strip()
        if 'domain' in options:
            options['domain'] = {'الحقيقية':'real','الحقيقيات':'real','المركبة':'complex','الصحيحة':'integer','reals':'real','integers':'integer'}.get(options['domain'],options['domain'])
        commands = [
            (r'(?:حل النظام|solve system|system)\s*[:：]?\s+(.+)', 'system', 'equations'),
            (r'(?:معكوس المصفوفة|معكوس|matrix inverse|inverse)\s*[:：]?\s+(.+)', 'matrix_inverse', 'matrix'),
            (r'(?:القيم والمتجهات الذاتية|القيم الذاتية|eigen)\s*[:：]?\s+(.+)', 'eigen', 'matrix'),
            (r'(?:اشتق|differentiate|derivative)\s*[:：]?\s+(.+)', 'differentiate', 'expression'),
            (r'(?:derivative-at)\s*[:：]?\s+(.+)', 'derivative_at', 'expression'),
            (r'(?:تكامل|integrate|integral)\s*[:：]?\s+(.+)', 'integrate', 'expression'),
            (r'(?:نهاية|limit)\s*[:：]?\s+(.+)', 'limit', 'expression'),
            (r'(?:جذر عددي|root)\s*[:：]?\s+(.+)', 'root', 'expression'),
            (r'(?:تحقق من الجذر|verify root)\s*[:：]?\s+(.+)', 'verify_root', 'expression'),
            (r'(?:حل صحيح|diophantine)\s*[:：]?\s+(.+)', 'diophantine', 'expression'),
            (r'(?:عظّم|عظم|maximize)\s*[:：]?\s+(.+)', 'optimize', 'expression'),
            (r'(?:صغّر|صغر|minimize)\s*[:：]?\s+(.+)', 'optimize', 'expression'),
            (r'(?:حل|solve)\s*[:：]?\s+(.+)', 'solve', 'expression'),
            (r'(?:احسب|calculate|evaluate)\s*[:：]?\s+(.+)', 'evaluate', 'expression'),
            (r'(?:إحصاء|احصاء|statistics)\s*[:：]?\s+(.+)', 'statistics', 'values'),
        ]
        request = None
        for pattern, operation, field in commands:
            match = re.fullmatch(pattern,text,re.I)
            if not match: continue
            value=match[1].strip()
            if field=='equations': value=[v.strip() for v in value.split('|')]
            if field in ('matrix','values'):
                value=json.loads(value,object_pairs_hook=unique_object,parse_float=str)
            request={'operation':operation,field:value,**options}
            if operation=='optimize': request.setdefault('sense','max' if re.match(r'عظ|عظّ|max',text,re.I) else 'min')
            break
        if request is None:
            # Preserve the previous word-problem grammar and its interpretation.
            try:
                from backend.reasoning_problem import parse_problem
                parse_problem(text)
            except ValueError:
                if re.fullmatch(r'[\w\s.()+*/^=<>!|−-]+',text,flags=re.UNICODE):
                    request={'operation':'solve' if '=' in text else 'inequality' if '<' in text or '>' in text else 'evaluate','expression':text,**options}
                else: raise ValueError('unsupported_math_wording')
            else:
                if options: raise ValueError('legacy_problem_does_not_accept_options')
                request={'operation':'legacy_problem','text':text}
        if request.get('operation')=='solve' and re.search(r'[<>]',request.get('expression','')):
            request['operation']='inequality'
        request=declared_from_text(request)
    bounded(request)
    if request.get('operation') not in OPERATIONS: raise ValueError('unsupported_math_operation')
    if any(key in request for key in ('verified','actor','role','destination','solver_status','execution_status')):
        raise ValueError('math_authority_fields_forbidden')
    return request


def solve_math(text):
    request = parse_math(text)
    if request['operation']=='batch':
        if set(request)!={'operation','requests'} or type(request['requests']) is not list or not 1<=len(request['requests'])<=4:
            raise ValueError('math_batch_limit')
        if any(type(r) is not dict or r.get('operation')=='batch' for r in request['requests']):
            raise ValueError('math_nested_batch_forbidden')
        children=[solve_math(json.dumps(r,ensure_ascii=False)) for r in request['requests']]
        complete=all(r['execution_status']=='completed' and r['problem_status'] in ('solved','no_solution','inconsistent','undefined') for r in children)
        result={'execution_status':'completed' if complete else 'partial','problem_status':'solved' if complete else 'unresolved',
                'result_kind':'conditional','completeness':'proved_in_domain' if all(r['completeness']=='proved_in_domain' for r in children) else 'not_proved',
                'result':{'results':children},'conditions':[],
                'verification':[{'check':'subproblem_coverage','status':'passed','detail':'Every requested formulation has its own result, domain and checks.'}],
                'explanation':['The results correspond to the requested formulations in order.']}
    elif request['operation']=='legacy_problem':
        if set(request)!={'operation','text'}: raise ValueError('unknown_math_field')
        from backend.reasoning_problem import solve_problem
        from backend.check_worker import Arithmetic
        old=solve_problem(request['text'],lambda expr:Arithmetic(expr).result())
        result={'execution_status':'completed' if old['status']=='ok' else 'partial',
                'problem_status':'solved' if old['status']=='ok' else 'undefined',
                'result_kind':'exact','completeness':'proved_in_domain' if old['status']=='ok' else 'not_proved',
                'result':{'calculations':old['results']},'conditions':[],
                'verification':[{'check':'exact_fraction','status':'pass' if old['status']=='ok' else 'partial','detail':old['scope']}],
                'explanation':['The recognized percentage or unit expression is computed exactly.'], 'legacy':old}
    else:
        from backend import math_discrete, math_symbolic
        solver = math_discrete if request['operation'] in math_discrete.OPERATIONS else math_symbolic
        try:result=solver.solve(request)
        except ValueError as exc:
            if str(exc)!='ambiguous_division_implicit_product':raise
            expression=request.get('expression','').translate(DIGITS).replace(' ','')
            match=re.fullmatch(r'([+\-]?\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)([A-Za-z][A-Za-z0-9]*)',expression)
            readings=[f'({match[1]}/{match[2]})*{match[3]}',f'{match[1]}/({match[2]}*{match[3]})'] if match else []
            result={'execution_status':'partial','problem_status':'ambiguous','result_kind':'conditional','completeness':'not_proved',
                    'result':{'readings':readings},'conditions':['For the denominator-product reading, the entire denominator must be nonzero.'],
                    'verification':[{'check':'notation_ambiguity','status':'passed','detail':'Implicit multiplication immediately after a division was not assigned a grouping.'}],
                    'explanation':['Specify parentheses to select the intended reading.']}
    result.update(version=VERSION,request=request,original_text=text,
                  input_sha256=hashlib.sha256(text.encode()).hexdigest())
    return result


def validate_math_result(output, text):
    if type(output) is not dict or output.get('version')!=VERSION or output.get('request')!=parse_math(text):
        raise ValueError('math_result_binding_mismatch')
    if output.get('original_text')!=text or output.get('input_sha256')!=hashlib.sha256(text.encode()).hexdigest():
        raise ValueError('math_original_binding_mismatch')
    axes={'execution_status':{'completed','partial','unavailable','limits_exceeded','cancelled'},
          'problem_status':{'solved','no_solution','inconsistent','undefined','ambiguous','insufficient_data','unresolved'},
          'result_kind':{'exact','approximate','conditional','parametric'},
          'completeness':{'proved_in_domain','search_limited','not_proved'}}
    for key,allowed in axes.items():
        if output.get(key) not in allowed: raise ValueError('invalid_math_status')
    if type(output.get('result')) is not dict: raise ValueError('invalid_math_result')
    for key in ('conditions','explanation'):
        if type(output.get(key)) is not list or len(output[key])>40 or any(type(x) is not str or len(x)>4096 for x in output[key]):
            raise ValueError('invalid_math_explanation')
    checks=output.get('verification')
    if type(checks) is not list or len(checks)>40 or any(type(c) is not dict or not all(type(c.get(k)) is str for k in ('check','status','detail')) for c in checks):
        raise ValueError('invalid_math_verification')
    if any(c['status'] not in ('passed','pass','failed','fail','partial','not_proved','not_run','unavailable','review_needed') for c in checks):
        raise ValueError('invalid_math_check_state')
    negative_candidate=output['request']['operation']=='verify_root' and output['result'].get('accepted') is False
    if output['problem_status'] in ('solved','no_solution','inconsistent') and (not checks or any(c['status'] in ('fail','failed') for c in checks) and not negative_candidate):
        raise ValueError('math_verification_missing_or_failed')
    if output['execution_status']!='completed' and output['problem_status']=='no_solution':
        raise ValueError('math_failure_is_not_no_solution')
    bounded(output['result'])
    if output['request']['operation']=='batch':
        expected=output['request']['requests'];children=output['result'].get('results')
        if type(children) is not list or len(children)!=len(expected):raise ValueError('math_batch_coverage_mismatch')
        for request,child in zip(expected,children):validate_math_result(child,json.dumps(request,ensure_ascii=False))
        complete=all(c['execution_status']=='completed' and c['problem_status'] in ('solved','no_solution','inconsistent','undefined') for c in children)
        if output['execution_status']!=('completed' if complete else 'partial') or output['problem_status']!=('solved' if complete else 'unresolved'):
            raise ValueError('math_batch_completion_mismatch')
        proved=all(c['completeness']=='proved_in_domain' for c in children)
        if output['completeness']!=('proved_in_domain' if proved else 'not_proved'):raise ValueError('math_batch_proof_mismatch')
    return output


def render_math(output, language='ar'):
    ar=language!='en'
    status={'solved':('النتيجة','Result'),'no_solution':('لا حل ضمن المجال المثبت','No solution in the verified domain'),
            'inconsistent':('المقدمات غير متسقة','Inconsistent premises'),'undefined':('غير معرّف','Undefined'),
            'ambiguous':('يلزم توضيح التجميع','Clarify the grouping'),'insufficient_data':('بيانات ناقصة','Missing data'),
            'unresolved':('غير محسوم','Unresolved')}[output['problem_status']][0 if ar else 1]
    if 'legacy' in output:
        from backend.reasoning_problem import render_problem
        return render_problem(output['legacy'],language)
    if output['request']['operation']=='batch':
        return '\n\n---\n\n'.join(render_math(child,language) for child in output['result']['results'])
    payload=output['result']
    lines=['**'+status+'**']
    domain=output['request'].get('domain','real')
    if output['request']['operation'] in {'solve','system','inequality','diophantine','optimize','differentiate','integrate','root','evaluate','matrix_inverse','eigen'}:
        lines.append(('المجال: ' if ar else 'Domain: ')+({'real':'الأعداد الحقيقية','complex':'الأعداد المركبة','integer':'الأعداد الصحيحة'}.get(domain,domain) if ar else domain))
    if 'value' in payload and set(payload)<={'value','decimal','decimal_exact'}:
        displayed=payload['decimal'] if payload.get('decimal_exact') is True else payload['value']
        lines.append('```text\n'+str(displayed)+'\n```')
    elif 'solutions' in payload and all(type(v) in (str,dict) for v in payload['solutions']):
        solutions=payload['solutions']
        if solutions:
            rendered=['; '.join(f'{k} = {v}' for k,v in row.items()) if type(row) is dict else (str(payload.get('variable','x'))+' = '+row) for row in solutions]
            lines.append('```text\n'+'\n'.join(rendered)+'\n```')
        if payload.get('parameters'):lines.append(('المعاملات الحرة: ' if ar else 'Free parameters: ')+', '.join(payload['parameters']))
    else:
        # Only mathematical output is displayed; operation hashes, input copies
        # and engine/resource receipts remain in the existing developer ledger.
        lines.append('```json\n'+json.dumps(payload,ensure_ascii=False,indent=2)+'\n```')
    kind={'exact':('دقيقة','Exact'),'approximate':('تقريبية','Approximate'),'conditional':('مشروطة','Conditional'),'parametric':('بارامترية','Parametric')}[output['result_kind']][0 if ar else 1]
    completeness={'proved_in_domain':('مثبت ضمن المجال المذكور','Established within the stated domain'),
                  'search_limited':('محدود بنطاق البحث','Limited to the search range'),
                  'not_proved':('اكتمال الحل غير مثبت','Completeness is not established')}[output['completeness']][0 if ar else 1]
    lines.append(kind+' · '+completeness)
    if output['conditions']: lines.append(('الشروط: ' if ar else 'Conditions: ')+'؛ '.join(output['conditions']))
    if output['explanation']: lines.extend(output['explanation'])
    if output['verification']: lines.append(('التحقق: ' if ar else 'Verification: ')+'؛ '.join(c['detail'] for c in output['verification']))
    lines.append('حساب بمحرك محلي ضمن الصيغة والمجال المعروضين؛ لا تحقق رسمي لبرهان ولا تفسير عام للنصوص أو الصور.' if ar else 'Local computation for the displayed formulation and domain; not formal proof-assistant verification or general prose/image interpretation.')
    return '\n\n'.join(lines)
