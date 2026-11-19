"""Trusted static HTML inspector, executed only by the isolated Python runner.

This is not a browser DOM or HTML constraint-validation implementation. It emits
bounded inert text and conservative form metadata; never HTML or executable code.
"""
from __future__ import annotations
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import re

VERSION = 'riwaq-static-web-v1'
MAX_INPUT = 512 * 1024
MAX_OUTPUT = 48000
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
HIDDEN = {'script','style','template','svg','noscript','iframe','object'}


def clean(value, maximum=500):
    if not isinstance(value, str): return ''
    return ' '.join(value.split())[:maximum]


class Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.parts=[]; self.title=[]; self.links=[]; self.forms=[]
        self.form=None; self.control=None; self.option=None; self.link=None
        self.issues=[]; self.dynamic=False; self.has_password=False; self.truncated=False
        self.nodes=0; self.characters=0

    def issue(self, reason):
        target=self.form['issues'] if self.form else self.issues
        if reason not in target: target.append(reason)

    def handle_starttag(self, tag, attrs):
        self.nodes+=1
        if self.nodes>10000: raise ValueError('HTML node limit exceeded')
        pairs=[(k,v or '') for k,v in attrs]; a=dict(pairs)
        hidden=bool(self.stack and self.stack[-1][1]) or tag in HIDDEN or 'hidden' in a or a.get('aria-hidden')=='true' or bool(re.search(r'(display\s*:\s*none|visibility\s*:\s*hidden)',a.get('style',''),re.I))
        if tag not in VOID:
            if len(self.stack)>=128: raise ValueError('HTML nesting limit exceeded')
            self.stack.append((tag,hidden))
        if tag=='script': self.dynamic=True
        if tag=='base': self.issues.append('base_url_override_not_supported')
        if hidden: return
        if tag=='a' and len(self.links)<32:
            self.link={'href':a.get('href','')[:4096],'text':''};self.links.append(self.link)
        if tag=='form':
            if self.form: self.issue('nested_form_not_supported');self.issues.append('nested_form_not_supported');return
            if len(self.forms)>=8: raise ValueError('Form count limit exceeded')
            self.form={'index':len(self.forms),'action':a.get('action','')[:4096],
                'method':a.get('method','get').lower(),'enctype':a.get('enctype','application/x-www-form-urlencoded').lower(),
                'fields':[],'issues':[]}
            self.forms.append(self.form)
            if len(a.get('action',''))>4096:self.issue('form_action_length_not_supported')
            if a.get('target') not in ('','_self',None):self.issue('alternate_form_target_not_supported')
            if len(a)!=len(pairs):self.issue('duplicate_attributes')
        if tag=='fieldset' and 'disabled' in a:self.issue('disabled_fieldset_not_supported')
        if tag=='optgroup':self.issue('grouped_options_not_supported')
        if tag in {'input','select','textarea','button'}:
            kind=a.get('type','text').lower() if tag=='input' else tag
            if kind=='password': self.has_password=True
            if 'form' in a:self.issues.append('external_form_association_not_supported')
            if not self.form:return
            if len(self.form['fields'])>=24:raise ValueError('Form field limit exceeded')
            if len(a)!=len(pairs):self.issue('duplicate_attributes')
            if any(k.startswith('on') for k in a):self.issue('scripted_control_not_supported')
            if any(k in a for k in ('formaction','formmethod','formenctype','formtarget')):self.issue('submission_override_not_supported')
            if tag=='button' or kind in {'submit','reset','button','image'}:return
            item={'name':a.get('name','')[:200],'type':kind,'required':'required' in a,
                  'disabled':'disabled' in a,'readonly':'readonly' in a,
                  'label':clean(a.get('aria-label') or a.get('placeholder') or a.get('name',''),200),
                  'maxlength':a.get('maxlength','')[:16], 'options':[]}
            # Never return hidden/default/password values. Explicit user values
            # are supplied later; page values cannot smuggle additional fields.
            self.form['fields'].append(item)
            if len(a.get('name',''))>200:self.issue('field_name_length_not_supported')
            if kind not in {'text','search','textarea','select'}:self.issue('unsupported_control_type')
            if 'multiple' in a:self.issue('multiple_values_not_supported')
            if any(k in a for k in ('pattern','minlength','dirname','list')):self.issue('advanced_validation_not_supported')
            if 'autocomplete' in a and any(x in a['autocomplete'].lower() for x in ('password','cc-','one-time-code')):self.issue('credential_or_payment_field')
            if kind in {'select','textarea'}:self.control=item
        if tag=='option' and self.control and self.control['type']=='select':
            if len(self.control['options'])>=20:raise ValueError('Option limit exceeded')
            self.option={'value':a.get('value'),'text':'','disabled':'disabled' in a}
            self.control['options'].append(self.option)
        if tag in {'p','div','li','br','h1','h2','h3','h4','h5','h6','tr','section'}:self.parts.append('\n')

    def handle_endtag(self, tag):
        hidden=bool(self.stack and self.stack[-1][1])
        if self.stack and self.stack[-1][0]==tag:self.stack.pop()
        elif tag not in VOID:
            match=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
            if match is not None:self.stack=self.stack[:match]
        if hidden:return
        if tag=='a':self.link=None
        if tag=='option' and self.option:
            self.option['text']=clean(self.option['text'],200)
            if self.option['value'] is None:self.option['value']=self.option['text']
            self.option=None
        if tag in {'select','textarea'}:self.control=None;self.option=None
        if tag=='form':self.form=None;self.control=None;self.option=None
        if tag in {'p','div','li','h1','h2','h3','h4','h5','h6','tr','section'}:self.parts.append('\n')

    def handle_data(self,data):
        if self.stack and self.stack[-1][1]:return
        if self.stack and self.stack[-1][0]=='title':self.title.append(data[:500])
        if self.link:self.link['text']=clean(self.link['text']+' '+data,200)
        if self.option:self.option['text']+=data[:200]
        # Form/default values and option labels are not page evidence.
        if self.form:return
        if self.characters<12000:
            keep=data[:12000-self.characters];self.parts.append(keep);self.characters+=len(keep)
            if len(keep)<len(data):self.truncated=True
        elif data.strip():self.truncated=True

    def result(self):
        if self.form:self.issue('unclosed_form_not_supported')
        text='\n'.join(clean(line,12000) for line in ''.join(self.parts).splitlines() if line.strip())
        for form in self.forms:
            names=[field['name'] for field in form['fields']]
            if any(not n for n in names) or len(set(names))!=len(names):form['issues'].append('missing_or_duplicate_field_names')
            if self.issues:form['issues'].extend(self.issues)
            form['issues']=list(dict.fromkeys(form['issues']))[:16]
        return {'title':clean(''.join(self.title),300),'text':text,'links':self.links,
                'forms':self.forms,'script_present':self.dynamic,'password_control_present':self.has_password,
                'truncated':self.truncated,'issues':list(dict.fromkeys(self.issues))[:16]}


def inspect_html(body):
    if not isinstance(body,bytes) or not 1<=len(body)<=MAX_INPUT:raise ValueError('HTML byte limit exceeded')
    text=body.decode('utf-8-sig',errors='strict')
    if '\x00' in text:raise ValueError('Binary HTML not supported')
    parser=Inspector();parser.feed(text);parser.close()
    result={'version':VERSION,'input_sha256':hashlib.sha256(body).hexdigest(),**parser.result()}
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if len(raw.encode())>MAX_OUTPUT:raise ValueError('HTML result limit exceeded')
    return result


def emit():
    body=Path('page.bin').read_bytes()
    print(json.dumps(inspect_html(body),ensure_ascii=False,allow_nan=False,separators=(',',':')))
