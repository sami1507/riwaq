"""Versioned synthetic routing evaluation. Semantic labels are not self-grading."""
import hashlib,json,re,unicodedata
from pathlib import Path

def canonical(value):
    return ' '.join(re.sub(r'[^\w\s]',' ',unicodedata.normalize('NFKC',value).casefold()).split())

def load_dataset(path):
    raw=Path(path).read_bytes()
    if len(raw)>1024*1024:raise ValueError('evaluation_dataset_size_limit')
    value=json.loads(raw);cases=value.get('cases')
    if not isinstance(cases,list) or not 1<=len(cases)<=1000 or not isinstance(value.get('version'),str):raise ValueError('invalid_evaluation_dataset')
    seen={};ids=set()
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or case['id'] in ids:raise ValueError('duplicate_evaluation_id')
        ids.add(case['id'])
        if case.get('split') not in {'development','holdout'} or case.get('runner') not in {'routing','human-review'} or type(case.get('critical')) is not bool:raise ValueError('invalid_evaluation_case')
        if not isinstance(case.get('input'),str) or not 1<=len(case['input'])<=8000:raise ValueError('invalid_evaluation_input')
        key=canonical(case['input'])
        if key in seen:raise ValueError('duplicate_or_cross_split_leakage')
        seen[key]=case['id']
        if case['runner']=='routing':
            for field in ('required_skills','allowed_skills'):
                if not isinstance(case.get(field),list) or any(not isinstance(x,str) for x in case[field]):raise ValueError('invalid_expected_selection')
            if not set(case['required_skills'])<=set(case['allowed_skills']):raise ValueError('contradictory_expected_selection')
    return value,hashlib.sha256(raw).hexdigest()

def run_routing(dataset, *, split=None):
    from backend import skill_core
    from backend.skill_loading import load_packages
    cases=[]
    for case in dataset['cases']:
        if case['runner']!='routing' or split and case['split']!=split:continue
        reasons=[];selected=[];chars=None
        try:
            intent=skill_core.classify(case['input']);selected=skill_core.select_skills(intent)
            loaded=load_packages(selected,skill_core.catalog(),skill_core.ROOT)
            chars=loaded['characters']
            if set(case['required_skills'])-set(selected):reasons.append('required_skill_missing')
            if set(selected)-set(case['allowed_skills']):reasons.append('unnecessary_skill_loaded')
            if set(case.get('forbidden_intents',[]))&set(intent.intent):reasons.append('forbidden_intent')
            if set(case.get('required_constraints',[]))-set(intent.constraints):reasons.append('constraint_missing')
            if set(case.get('required_outputs',[]))-set(intent.requestedOutputs):reasons.append('output_missing')
            if len(loaded['loaded'])>case.get('max_loaded',8):reasons.append('excessive_instruction_loading')
            if loaded['executed_scripts']:reasons.append('unexpected_script_execution')
        except (ValueError,KeyError,OSError):reasons.append('routing_or_package_rejected')
        cases.append({'id':case['id'],'split':case['split'],'critical':case['critical'],'status':'failed' if reasons else 'passed','reason_codes':reasons,'selected':selected,'instruction_characters':chars,'kind':'routing_contract'})
    return cases

def totals(cases):
    return {key:sum(c['status']==key for c in cases) for key in ('passed','failed','skipped','blocked')}
