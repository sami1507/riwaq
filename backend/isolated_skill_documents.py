"""Document transformation kernels, called only inside the isolated worker.

Pure functions are testable with controlled fixtures. Production never calls
execute_documents on host-owned uploaded bytes. No application/network imports.
"""
from __future__ import annotations
import copy
import csv
from difflib import SequenceMatcher
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET
from backend.skill_transform_protocol import TransformError as Error,output_artifact,dump,MAX_OUTPUT

OPS={'pdf-create','pdf-form-fill','pdf-merge','pdf-split','pdf-redact','docx-edit','docx-compare','template-fill','convert','table-extract'}
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'w':W};XMLSPACE='{http://www.w3.org/XML/1998/namespace}space'
TOKEN=re.compile(r'\{\{([A-Za-z][A-Za-z0-9_.]{0,79})\}\}')

def _keys(value,allowed,required=()):
    if not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value):raise Error('document_options_invalid')
def _text(value,limit=4000,empty=False):
    if not isinstance(value,str) or len(value)>limit or (not empty and not value) or any(ord(c)<32 and c not in '\n\r\t' for c in value):raise Error('document_text_invalid')
    return value
def _integer(value,low=1,high=200):
    if type(value) is not int or not low<=value<=high:raise Error('document_integer_invalid')
    return value
def _boolean(value):
    if type(value) is not bool:raise Error('document_boolean_invalid')
def _scalar(value):
    if value is None:return ''
    if type(value) is bool:return 'true' if value else 'false'
    if isinstance(value,str):return _text(value,4000,True)
    if type(value) in (int,float) and math.isfinite(value) and len(str(value))<=80:return str(value)
    raise Error('template_value_invalid')
def _list(value,maximum=100,minimum=0):
    if not isinstance(value,list) or not minimum<=len(value)<=maximum:raise Error('document_list_limit')
    return value

def validate_request(value):
    _keys(value,{'operation','sourceIds','options'},{'operation','sourceIds','options'})
    op=value['operation'];ids=value['sourceIds'];options=value['options']
    if op not in OPS:raise Error('document_operation_unsupported')
    _list(ids,8)
    if len(set(ids))!=len(ids) or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',x) for x in ids):raise Error('document_source_ids_invalid')
    count=0 if op=='pdf-create' else 2 if op=='docx-compare' else 1
    if op not in {'pdf-create','pdf-merge'} and len(ids)!=count:raise Error('document_source_count')
    if op=='pdf-merge' and not 1<=len(ids)<=8:raise Error('document_source_count')
    if op=='pdf-create':
        _keys(options,{'title','paragraphs','tables','direction','images'},{'title'})
        _text(options['title'],200)
        for p in _list(options.get('paragraphs',[]),200):
            if isinstance(p,str):_text(p)
            else:
                _keys(p,{'text','style'},{'text'});_text(p['text'])
                if p.get('style','normal') not in {'normal','heading1','heading2','bullet'}:raise Error('paragraph_style_unsupported')
        if options.get('direction','rtl') not in {'rtl','ltr'}:raise Error('document_direction')
        for table in _list(options.get('tables',[]),10):
            _keys(table,{'headers','rows'},{'headers','rows'});_list(table['headers'],8,1);_list(table['rows'],200)
            for text in table['headers']:_text(text,500)
            for row in table['rows']:
                if not isinstance(row,list) or len(row)!=len(table['headers']):raise Error('table_dimensions')
                for text in row:_text(text,1000,True)
        for image in _list(options.get('images',[]),8):
            _keys(image,{'sourceId','caption'},{'sourceId','caption'});_text(image['caption'],500)
            if image['sourceId'] not in ids:raise Error('image_source_missing')
    elif op in {'pdf-merge','pdf-split'}:
        _keys(options,{'selections','allowRepeatedPages'},{'selections'})
        seen=set()
        for selection in _list(options['selections'],8,1):
            _keys(selection,{'sourceId','pages'},{'sourceId','pages'})
            if selection['sourceId'] not in ids:raise Error('page_source_missing')
            for page in _list(selection['pages'],200,1):
                _integer(page);key=(selection['sourceId'],page)
                if key in seen and options.get('allowRepeatedPages') is not True:raise Error('repeated_pages_require_explicit_option')
                seen.add(key)
        if 'allowRepeatedPages' in options:_boolean(options['allowRepeatedPages'])
        if sum(len(s['pages']) for s in options['selections'])>200:raise Error('page_limit')
    elif op=='pdf-form-fill':
        _keys(options,{'values','requiredFields'},{'values'})
        if not isinstance(options['values'],dict) or not 1<=len(options['values'])<=100:raise Error('form_values_limit')
        for key,value in options['values'].items():
            _text(key,200)
            if isinstance(value,list):
                for text in _list(value,30):_text(text,1000,True)
            elif type(value) is not bool:_text(value,4000,True)
        for key in _list(options.get('requiredFields',[]),100):_text(key,200)
    elif op=='pdf-redact':
        _keys(options,{'regions','allowRasterization'},{'regions','allowRasterization'})
        if options['allowRasterization'] is not True:raise Error('raster_redaction_requires_explicit_consent')
        for region in _list(options['regions'],100,1):
            _keys(region,{'page','x0','y0','x1','y1'},{'page','x0','y0','x1','y1'});_integer(region['page'],1,20)
            for key in ('x0','y0','x1','y1'):
                x=region[key]
                if type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1:raise Error('redaction_coordinates_invalid')
            if region['x0']>=region['x1'] or region['y0']>=region['y1']:raise Error('redaction_rectangle_empty')
    elif op=='docx-edit':
        _keys(options,{'edits'},{'edits'})
        targets=set()
        for edit in _list(options['edits'],100,1):
            _keys(edit,{'part','paragraph','table','row','column','expectedText','replacement'},{'expectedText','replacement'})
            part=edit.get('part','word/document.xml')
            if not re.fullmatch(r'word/(?:document|header[1-9][0-9]?|footer[1-9][0-9]?)\.xml',part):raise Error('docx_part_unsupported')
            _text(edit['expectedText'],4000,True);_text(edit['replacement'],4000,True)
            if 'paragraph' in edit:
                _integer(edit['paragraph'],1,5000)
                if any(k in edit for k in ('table','row','column')):raise Error('ambiguous_docx_target')
            else:
                for key in ('table','row','column'):_integer(edit.get(key),1,500)
            target=(part,edit.get('paragraph'),edit.get('table'),edit.get('row'),edit.get('column'))
            if target in targets:raise Error('duplicate_docx_target')
            targets.add(target)
    elif op=='docx-compare':
        _keys(options,{'ignoreWhitespace'})
        if 'ignoreWhitespace' in options:_boolean(options['ignoreWhitespace'])
    elif op=='template-fill':
        _keys(options,{'values','optionalFields','repeatRows'},{'values'})
        if not isinstance(options['values'],dict) or len(options['values'])>200:raise Error('template_values_limit')
        optional=_list(options.get('optionalFields',[]),200)
        for key in optional:
            if not isinstance(key,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',key):raise Error('template_field_name')
        for key,value in options['values'].items():
            if not isinstance(key,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',key):raise Error('template_field_name')
            if value is None and key not in optional:raise Error('required_template_null')
            _scalar(value)
        groups=options.get('repeatRows',{})
        if not isinstance(groups,dict) or len(groups)>10:raise Error('template_repeat_limit')
        for key,rows in groups.items():
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',key):raise Error('template_repeat_name')
            for row in _list(rows,100):
                if not isinstance(row,dict) or not 1<=len(row)<=20:raise Error('template_repeat_row')
                for field,val in row.items():
                    if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}',field) or val is None:raise Error('template_repeat_field')
                    _scalar(val)
    elif op=='convert':
        _keys(options,{'format','allowReflow','direction'},{'format'})
        if options['format'] not in {'txt','pdf','csv'}:raise Error('conversion_format_unsupported')
        if 'allowReflow' in options:_boolean(options['allowReflow'])
        if options.get('direction','rtl') not in {'rtl','ltr'}:raise Error('document_direction')
    elif op=='table-extract':_keys(options,{'tableIndex'})
    if 'tableIndex' in options:_integer(options['tableIndex'],1,100)
    if len(dump(options))>200000:raise Error('document_options_limit')
    return value

def _source_ref(source,**location):
    return {'source_id':source['id'],'sha256':source['sha256'],'version':source['version'],**location}

def _pdf(source,allow_forms=False,allow_metadata=False):
    from pypdf import PdfReader
    raw=source['content']
    if not raw.startswith(b'%PDF-'):raise Error('expected_pdf_source')
    try:reader=PdfReader(io.BytesIO(raw),strict=True)
    except Exception:raise Error('pdf_corrupt') from None
    if reader.is_encrypted:raise Error('pdf_encrypted_unsupported')
    if not 1<=len(reader.pages)<=200:raise Error('pdf_page_limit')
    root=reader.trailer['/Root']
    forbidden={'/JavaScript','/JS','/AA','/Launch','/RichMedia','/XFA','/GoToR','/SubmitForm','/ImportData'}
    if not allow_metadata:forbidden|={'/EmbeddedFiles','/Filespec'}
    seen=set();stack=[root];count=0
    from pypdf.generic import IndirectObject,DictionaryObject,ArrayObject
    while stack:
        obj=stack.pop()
        if isinstance(obj,IndirectObject):
            key=(obj.idnum,obj.generation)
            if key in seen:continue
            seen.add(key);obj=obj.get_object()
        count+=1
        if count>30000:raise Error('pdf_object_limit')
        if isinstance(obj,DictionaryObject):
            if set(obj)&forbidden:raise Error('pdf_active_or_embedded_content_unsupported')
            if '/OpenAction' in obj:
                action=obj['/OpenAction']
                # ISO PDF permits a passive page-view destination here. An
                # action dictionary (JavaScript, Launch, URI, etc.) is rejected.
                if not isinstance(action,ArrayObject) or not 2<=len(action)<=6 or str(action[1]) not in {'/Fit','/FitH','/FitV','/FitR','/FitB','/FitBH','/FitBV','/XYZ'}:raise Error('pdf_active_or_embedded_content_unsupported')
            if '/ByteRange' in obj or obj.get('/FT')=='/Sig':raise Error('signed_pdf_unsupported')
            stack.extend(obj.values())
        elif isinstance(obj,ArrayObject):stack.extend(obj)
    if '/AcroForm' in root and not allow_forms:raise Error('pdf_forms_require_dedicated_operation')
    return reader

def _pdf_output(writer,filename,expected_pages,limitations=()):
    from pypdf import PdfReader
    stream=io.BytesIO();writer.write(stream);raw=stream.getvalue()
    check=PdfReader(io.BytesIO(raw),strict=True)
    if len(check.pages)!=expected_pages:raise Error('pdf_page_verification_failed')
    return output_artifact(raw,filename,{'package_reopened':True,'pages':len(check.pages),'office_rendered':False},limitations)

def _merge_split(request,sources):
    from pypdf import PdfWriter
    readers={s['id']:_pdf(s) for s in sources};by_id={s['id']:s for s in sources};selections=request['options']['selections']
    groups=[selections] if request['operation']=='pdf-merge' else [[s] for s in selections]
    artifacts=[];refs=[]
    for output_index,group in enumerate(groups,1):
        writer=PdfWriter();output_page=0
        for selection in group:
            reader=readers[selection['sourceId']]
            if reader.outline:raise Error('pdf_bookmarks_preservation_unsupported')
            for page_number in selection['pages']:
                if page_number>len(reader.pages):raise Error('pdf_page_not_found')
                page=reader.pages[page_number-1]
                # Link destination remapping and field-name collisions must not be guessed.
                if page.get('/Annots'):raise Error('pdf_annotation_remap_unsupported')
                writer.add_page(page);output_page+=1
                refs.append(_source_ref(by_id[selection['sourceId']],page=page_number,output_file=output_index,output_page=output_page))
        filename='riwaq-merged.pdf' if request['operation']=='pdf-merge' else f'riwaq-part-{output_index}.pdf'
        artifacts.append(_pdf_output(writer,filename,output_page,('Forms, bookmarks and annotations require an unsupported preservation workflow and are rejected before writing.',)))
    return {'method':'pypdf-page-copy','outputs':len(artifacts),'source_originals_modified':False},artifacts,refs

def _fill_form(request,source):
    from pypdf import PdfWriter,PdfReader
    reader=_pdf(source,allow_forms=True);fields=reader.get_fields() or {}
    if not fields:raise Error('pdf_has_no_interactive_form')
    options=request['options'];values=options['values'];required=set(options.get('requiredFields',[]))
    for key,field in fields.items():
        if int(field.get('/Ff',0))&2:required.add(key)
    if set(values)-set(fields) or required-set(fields):raise Error('form_field_not_found')
    if any((values.get(key,fields[key].get('/V')) in (None,'','/Off',False)) for key in required):raise Error('required_form_field_missing')
    actual={}
    for key,value in values.items():
        field=fields[key];kind=field.get('/FT');flags=int(field.get('/Ff',0))
        if flags&1:raise Error('readonly_form_field')
        if kind=='/Tx':
            if not isinstance(value,str):raise Error('form_text_type_required')
            # Existing embedded-font mappings are not proven for arbitrary Unicode replacement.
            if any(ord(c)>126 or ord(c)<32 and c!='\n' for c in value):raise Error('form_unicode_appearance_unverified')
            if '\n' in value and not flags&(1<<12):raise Error('form_multiline_not_supported_by_field')
            if len(value)>int(field.get('/MaxLen',4000)):raise Error('form_maximum_length')
            actual[key]=value
        elif kind=='/Btn':
            if flags&(1<<16):raise Error('form_pushbutton_unsupported')
            states={str(x) for x in field.get('/_States_',[])}
            for widget in field.get('/Kids',[]) or [field]:
                states.update(str(k) for k in widget.get_object().get('/AP',{}).get('/N',{}))
            states.discard('/Off')
            if type(value) is bool:
                if value and len(states)!=1:raise Error('form_button_state_ambiguous')
                actual[key]=next(iter(states)) if value else '/Off'
            elif isinstance(value,str) and value in states|{'/Off'}:actual[key]=value
            else:raise Error('form_button_state_invalid')
        elif kind=='/Ch':
            allowed={str(x[0] if isinstance(x,list) else x) for x in field.get('/Opt',[])}
            selected=value if isinstance(value,list) else [value]
            if any(not isinstance(x,str) or x not in allowed for x in selected):raise Error('form_choice_invalid')
            if any(ord(c)>126 for x in selected for c in x):raise Error('form_unicode_appearance_unverified')
            if len(selected)>1 and not flags&(1<<21):raise Error('form_multiselect_not_supported_by_field')
            actual[key]=value
        else:raise Error('form_field_type_unsupported')
    writer=PdfWriter();writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(None,actual,auto_regenerate=False)
    artifact=_pdf_output(writer,'riwaq-filled.pdf',len(reader.pages),('Unicode text-field appearances are not validated and are rejected; no signature is applied.',))
    reopened=PdfReader(io.BytesIO(artifact.content));after=reopened.get_fields() or {}
    for key,value in actual.items():
        if key not in after or after[key].get('/V')!=value:raise Error('form_value_verification_failed')
    for key,field in fields.items():
        if key not in actual and after.get(key,{}).get('/V')!=field.get('/V'):raise Error('untargeted_form_field_changed')
    return {'method':'pypdf-acroform','fields_updated':list(actual),'values_verified':True,'appearance_review_required':True},[artifact],[_source_ref(source,field=key) for key in actual]

def _docx(source):
    from backend.document_extract import Archive,Limits
    archive=Archive(source['content'],Limits());names=archive.names
    if 'word/document.xml' not in names:raise Error('expected_docx_source')
    if any(n.startswith(('_xmlsignatures/','word/embeddings/')) for n in names):raise Error('signed_or_embedded_docx_unsupported')
    parts={n:archive.read(n) for n in names}
    for n,raw in parts.items():
        if n.endswith('.rels'):
            root=archive.tree(n)
            for rel in root:
                if rel.get('TargetMode')=='External' and 'hyperlink' not in rel.get('Type',''):raise Error('external_docx_resource_unsupported')
        if n.startswith('word/') and n.endswith('.xml'):
            tree=archive.tree(n)
            if any(x.tag in {'{'+W+'}ins','{'+W+'}del','{'+W+'}moveFrom','{'+W+'}moveTo','{'+W+'}altChunk','{'+W+'}object','{'+W+'}instrText','{'+W+'}fldSimple'} for x in tree.iter()):raise Error('tracked_changes_or_active_fields_unsupported')
    return archive,parts

def _text_node(p):
    from backend.document_extract import text_runs
    return text_runs(p,W)
def _body(root):
    return root.find('w:body',NS) if root.tag=='{'+W+'}document' else root
def _serialize(root,original):
    # mc:Ignorable and other QName-valued attributes refer to namespace prefixes.
    # ElementTree normally drops unused declarations; keep those declarations
    # and prefixes so an untouched compatibility attribute cannot become invalid.
    namespaces={}
    for _,pair in ET.iterparse(io.BytesIO(original),events=['start-ns']):
        prefix,uri=pair
        if prefix not in namespaces:namespaces[prefix]=uri
        elif namespaces[prefix]!=uri:raise Error('docx_namespace_rebinding_unsupported')
    for prefix,uri in namespaces.items():
        if re.fullmatch(r'ns\d+',prefix):raise Error('docx_reserved_namespace_prefix')
        ET.register_namespace(prefix,uri)
    raw=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    first=raw.find(b'>',raw.find(b'?>')+2);head=raw[:first]
    for prefix,uri in namespaces.items():
        attr=('xmlns:'+prefix if prefix else 'xmlns').encode()
        if not re.search(rb'\s'+re.escape(attr)+rb'=',head):
            from xml.sax.saxutils import quoteattr
            head+=b' '+attr+b'='+quoteattr(uri).encode('utf-8')
    return head+raw[first:]
def _replace_paragraph(p,replacement):
    if any(x.tag.rsplit('}',1)[-1] in {'hyperlink','drawing','pict','fldSimple','sdt','smartTag'} for x in p.iter()):raise Error('target_paragraph_complex_content_unsupported')
    nodes=list(p.iter('{'+W+'}t'))
    if not nodes:
        run=ET.SubElement(p,'{'+W+'}r');nodes=[ET.SubElement(run,'{'+W+'}t')]
    for node in nodes:node.text=''
    for parent in p.iter():
        for child in list(parent):
            if child.tag in {'{'+W+'}tab','{'+W+'}br','{'+W+'}cr'}:parent.remove(child)
    # OOXML uses elements for visible breaks/tabs; literal whitespace in w:t is
    # not a reliable editable Word line-break representation.
    first=nodes[0];parent=next(x for x in p.iter() if first in list(x));index=list(parent).index(first)
    parent.remove(first)
    for piece in re.split(r'(\r\n|\r|\n|\t)',replacement):
        if not piece:continue
        if piece in ('\r\n','\r','\n','\t'):node=ET.Element('{'+W+'}'+('tab' if piece=='\t' else 'br'))
        else:node=ET.Element('{'+W+'}t');node.text=piece;node.set(XMLSPACE,'preserve')
        parent.insert(index,node);index+=1

def _docx_write(parts,original,changed,filename):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for key,raw in parts.items():out.writestr(key,raw)
    raw=stream.getvalue();fake={'content':raw};_,check=_docx(fake)
    if set(check)!=set(parts) or any(check[k]!=v for k,v in parts.items()):raise Error('docx_package_verification_failed')
    if any(parts[k]!=v for k,v in original.items() if k not in changed):raise Error('untargeted_docx_part_changed')
    return output_artifact(raw,filename,{'package_reopened':True,'untargeted_parts_unchanged':True,'changed_parts':sorted(changed),'office_rendered':False},
                           ('Word/LibreOffice native layout has not been rendered; edited text stays editable and package parts outside the target are preserved.',))

def _edit_docx(request,source):
    archive,original=_docx(source);parts=dict(original);trees={};refs=[]
    for edit in request['options']['edits']:
        part=edit.get('part','word/document.xml')
        if part not in parts:raise Error('docx_part_not_found')
        tree=trees.setdefault(part,archive.tree(part));body=_body(tree)
        try:
            if 'paragraph' in edit:p=body.findall('w:p',NS)[edit['paragraph']-1]
            else:
                table=body.findall('w:tbl',NS)[edit['table']-1];cell=table.findall('w:tr',NS)[edit['row']-1].findall('w:tc',NS)[edit['column']-1]
                if cell.find('w:tcPr/w:gridSpan',NS) is not None or cell.find('w:tcPr/w:vMerge',NS) is not None:raise Error('merged_cell_edit_unsupported')
                paragraphs=cell.findall('w:p',NS)
                if len(paragraphs)!=1:raise Error('multi_paragraph_cell_edit_unsupported')
                p=paragraphs[0]
        except IndexError:raise Error('docx_target_not_found') from None
        if _text_node(p)!=edit['expectedText']:raise Error('docx_target_text_changed')
        _replace_paragraph(p,edit['replacement'])
        if _text_node(p)!=edit['replacement']:raise Error('docx_edit_verification_failed')
        refs.append(_source_ref(source,part=part,**{k:edit[k] for k in ('paragraph','table','row','column') if k in edit}))
    for part,tree in trees.items():parts[part]=_serialize(tree,original[part])
    artifact=_docx_write(parts,original,set(trees),'riwaq-edited.docx')
    return {'method':'exact-ooxml-target-edit','edits_applied':len(refs),'source_original_modified':False},[artifact],refs

def _records(source):
    archive,_=_docx(source);root=archive.tree('word/document.xml');body=_body(root);records=[];pi=ti=0
    for element in body:
        if element.tag=='{'+W+'}p':
            pi+=1;records.append({'text':_text_node(element),'location':{'part':'word/document.xml','paragraph':pi}})
        elif element.tag=='{'+W+'}tbl':
            ti+=1
            for ri,row in enumerate(element.findall('w:tr',NS),1):
                for ci,cell in enumerate(row.findall('w:tc',NS),1):records.append({'text':'\n'.join(_text_node(p) for p in cell.findall('w:p',NS)),'location':{'part':'word/document.xml','table':ti,'row':ri,'column':ci}})
    if len(records)>1000 or sum(len(r['text']) for r in records)>64000:raise Error('comparison_scope_limit')
    return records

def _compare(request,sources):
    before,after=map(_records,sources);normalize=(lambda x:' '.join(x.split())) if request['options'].get('ignoreWhitespace',False) else (lambda x:x)
    a=[normalize(x['text']) for x in before];b=[normalize(x['text']) for x in after];changes=[];refs=[]
    for kind,a0,a1,b0,b1 in SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes():
        if kind=='equal':continue
        left=before[a0:a1];right=after[b0:b1]
        for records,source in ((left,sources[0]),(right,sources[1])):
            refs.extend(_source_ref(source,**record['location']) for record in records)
        old='\n'.join(x['text'] for x in left);new='\n'.join(x['text'] for x in right)
        numbers=lambda s:re.findall(r'[-+]?\d+(?:[.,]\d+)*',s)
        negation=lambda s:re.findall(r'\b(?:not|no|never|لا|ليس|لم|لن|לא|אין)\b',s,re.I)
        changes.append({'kind':{'replace':'modified','insert':'added','delete':'deleted'}[kind],'before':left,'after':right,
                        'numeric_literals_changed':numbers(old)!=numbers(new),'negation_markers_changed':negation(old)!=negation(new)})
    deleted={r['text'] for c in changes if c['kind']=='deleted' for r in c['before'] if r['text']}
    added={r['text'] for c in changes if c['kind']=='added' for r in c['after'] if r['text']}
    return {'method':'bounded-ooxml-sequence-comparison','changes':changes,'moved_candidates':sorted(deleted&added),
            'whitespace_ignored':request['options'].get('ignoreWhitespace',False),'limitations':['Body paragraphs and cell text only; headers, images, formatting and semantic equivalence are not compared. Moved candidates are not certified identity matches.']},[],refs

def _template(request,source):
    archive,original=_docx(source);parts=dict(original);options=request['options'];values=dict(options['values']);optional=set(options.get('optionalFields',[]));groups=options.get('repeatRows',{});used=set();group_use=set();changed=set();count=0
    for field in optional:values.setdefault(field,None)
    def populate(p,row_values=None):
        nonlocal count
        raw=_text_node(p);tokens=list(TOKEN.finditer(raw))
        if not tokens:return
        mapping=values| (row_values or {})
        missing={m[1] for m in tokens}-set(mapping)
        if missing:raise Error('template_required_field_missing')
        # A single pass over original spans: replacement text cannot become a directive.
        output=[];end=0
        for match in tokens:
            key=match[1];used.add(key);output.extend((raw[end:match.start()],_scalar(mapping[key])));end=match.end();count+=1
        output.append(raw[end:]);text=''.join(output)
        if len(text)>8000:raise Error('template_expanded_text_limit')
        _replace_paragraph(p,text)
    for part in sorted(parts):
        if not re.fullmatch(r'word/(?:document|header[1-9][0-9]?|footer[1-9][0-9]?)\.xml',part):continue
        tree=archive.tree(part);changed_part=False;processed=set()
        for table in list(tree.iter('{'+W+'}tbl')):
            for row in list(table.findall('w:tr',NS)):
                names={m[1].split('.',1)[0] for p in row.iter('{'+W+'}p') for m in TOKEN.finditer(_text_node(p)) if '.' in m[1]}
                if not names:continue
                if len(names)!=1 or not names<=set(groups):raise Error('template_repeat_group_missing')
                group=next(iter(names))
                if group in group_use:raise Error('template_repeat_group_ambiguous')
                group_use.add(group);position=list(table).index(row)
                if any(x.tag in {'{'+W+'}vMerge','{'+W+'}gridSpan'} for x in row.iter()):raise Error('template_repeat_merged_row_unsupported')
                fields={m[1].split('.',1)[1] for p in row.iter('{'+W+'}p') for m in TOKEN.finditer(_text_node(p)) if m[1].startswith(group+'.')}
                for index,record in enumerate(groups[group]):
                    if set(record)-fields:raise Error('template_repeat_field_not_found')
                    clone=copy.deepcopy(row)
                    for p in clone.iter('{'+W+'}p'):populate(p,{group+'.'+k:v for k,v in record.items()});processed.add(id(p))
                    table.insert(position+index,clone)
                table.remove(row);changed_part=True
        for p in tree.iter('{'+W+'}p'):
            if id(p) in processed:continue
            if TOKEN.search(_text_node(p)):populate(p);changed_part=True
        if changed_part:parts[part]=_serialize(tree,original[part]);changed.add(part)
    if set(groups)!=group_use:raise Error('template_repeat_group_not_found')
    if not changed:raise Error('template_has_no_matching_fields')
    if set(values)-used-optional:raise Error('template_supplied_field_not_found')
    artifact=_docx_write(parts,original,changed,'riwaq-populated.docx')
    return {'method':'literal-one-pass-ooxml-template','substitutions':count,'repeat_groups':sorted(group_use),'source_original_modified':False},[artifact],[_source_ref(source,part=part) for part in sorted(changed)]

def _tables(source,index=None):
    archive,_=_docx(source);body=_body(archive.tree('word/document.xml'));result=[];refs=[]
    for ti,table in enumerate(body.findall('w:tbl',NS),1):
        if index and ti!=index:continue
        cells=[];headers=[]
        for ri,row in enumerate(table.findall('w:tr',NS),1):
            column=1
            if row.find('w:trPr/w:tblHeader',NS) is not None:headers.append(ri)
            for entry in row.findall('w:tc',NS):
                raw='\n'.join(_text_node(p) for p in entry.findall('w:p',NS));span=entry.find('w:tcPr/w:gridSpan',NS);merge=entry.find('w:tcPr/w:vMerge',NS)
                colspan=int(span.get('{'+W+'}val','1')) if span is not None else 1
                if not 1<=colspan<=100:raise Error('table_merge_limit')
                loc={'part':'word/document.xml','table':ti,'row':ri,'column':column}
                cells.append({'raw':raw,'value':raw,'interpretation':'text','row':ri,'column':column,'column_span':colspan,
                              'vertical_merge':None if merge is None else merge.get('{'+W+'}val','continue'),'location':loc})
                refs.append(_source_ref(source,**loc));column+=colspan
                if len(cells)>5000:raise Error('table_cell_limit')
        result.append({'index':ti,'header_rows':headers,'cells':cells,'row_count':len(table.findall('w:tr',NS))})
    if not result:raise Error('table_not_found')
    return {'tables':result,'method':'native-docx-cell-structure','limitations':['Values remain raw text: empty, zero and leading zeros are distinct. No inferred PDF/OCR grid, page-spanning merge, units or footnote semantics.']},refs

def _fonts():
    from fontTools.ttLib import TTFont
    folder=Path('/opt/riwaq/fonts');out=Path('/tmp/riwaq-fonts');out.mkdir(exist_ok=True)
    fonts={};coverage=set()
    for family,filename in [('plex-ar','IBMPlexSansArabic-Regular.woff2'),('plex-he','IBMPlexSansHebrew-Regular.woff2'),('plex-latin','IBMPlexSans-Regular.woff2')]:
        target=out/(family+'.ttf');font=TTFont(folder/filename);coverage.update(font.getBestCmap());font.flavor=None;font.save(target);font.close();fonts[family]=str(target)
    return fonts,coverage

def _create_pdf(options,sources):
    from fpdf import FPDF
    from pypdf import PdfReader
    import pypdfium2 as pdfium
    from PIL import Image
    fonts,coverage=_fonts();all_text=[options['title']]+[(x if isinstance(x,str) else x['text']) for x in options.get('paragraphs',[])]+[t for table in options.get('tables',[]) for row in [table['headers']]+table['rows'] for t in row]+[x['caption'] for x in options.get('images',[])]
    if sum(map(len,all_text))>64000:raise Error('pdf_text_limit')
    if any(ord(c) not in coverage and c not in '\n\r\t' for text in all_text for c in text):raise Error('pdf_font_missing_glyph')
    pdf=FPDF(format='A4');pdf.set_margins(18,18,18);pdf.set_auto_page_break(True,18)
    for family,path in fonts.items():pdf.add_font(family,fname=path)
    pdf.set_font('plex-ar',size=12);pdf.set_fallback_fonts(['plex-he','plex-latin']);pdf.set_text_shaping(True)
    pdf.set_title(options['title']);pdf.set_author('');pdf.set_creator('Riwaq');pdf.add_page()
    alignment='R' if options.get('direction','rtl')=='rtl' else 'L'
    def paragraph(text,size=12):
        pdf.set_font('plex-ar',size=size)
        pdf.multi_cell(0,size*.55,text=text,align=alignment,new_x='LMARGIN',new_y='NEXT');pdf.ln(2)
        if len(pdf.pages)>40:raise Error('pdf_page_limit')
    paragraph(options['title'],20)
    for item in options.get('paragraphs',[]):
        text=item if isinstance(item,str) else item['text'];style='normal' if isinstance(item,str) else item.get('style','normal')
        paragraph(('• '+text) if style=='bullet' else text,16 if style=='heading1' else 14 if style=='heading2' else 12)
    pdf.set_font('plex-ar',size=10)
    from fpdf.fonts import FontFace
    for data in options.get('tables',[]):
        with pdf.table(line_height=6,headings_style=FontFace(emphasis='',fill_color=(232,238,235)),text_align=alignment,repeat_headings=1) as table:
            for values in [data['headers']]+data['rows']:
                row=table.row()
                for text in values:row.cell(text)
        pdf.ln(4)
    by_id={s['id']:s for s in sources}
    for item in options.get('images',[]):
        source=by_id[item['sourceId']];image=Image.open(io.BytesIO(source['content']))
        if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height>8_000_000:raise Error('pdf_image_limit')
        image.load();pdf.image(image,w=pdf.epw,h=75,keep_aspect_ratio=True);paragraph(item['caption'],10);image.close()
    raw=bytes(pdf.output())
    reader=PdfReader(io.BytesIO(raw));text='\n'.join(p.extract_text() for p in reader.pages)
    # Numeric/source literals are acceptance checks, not an OCR confidence score.
    expected_numbers=re.findall(r'\d+(?:[.,]\d+)*','\n'.join(all_text))
    if any(number not in text for number in expected_numbers):raise Error('pdf_numeric_text_verification_failed')
    if len(reader.pages)>40:raise Error('pdf_page_limit')
    document=pdfium.PdfDocument(raw)
    for page in document:
        bitmap=page.render(scale=.5);bitmap.close();page.close()
    document.close()
    logical_exact=all(' '.join(t.split()) in ' '.join(text.split()) for t in all_text if t.strip())
    result={'method':'fpdf2-harfbuzz-bidi','pages':len(reader.pages),'searchable_text':True,'numbers_verified':True,
            'logical_text_roundtrip_exact':logical_exact,'render_decode_all_pages':True,'visual_review_complete':False,'source_characters':sum(map(len,all_text))}
    limitations=('All pages were decoded, not visually approved. Native print-layout acceptance requires visual review.',)
    if not logical_exact:limitations+=('Logical extraction order is not exact for all source text; RTL search/copy fidelity needs review.',)
    return result,[output_artifact(raw,'riwaq-created.pdf',result,limitations)],[_source_ref(s,role='image') for s in sources]

def _redact(request,source):
    from PIL import ImageDraw
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    reader=_pdf(source,allow_forms=True,allow_metadata=True)
    if len(reader.pages)>20:raise Error('redaction_page_limit')
    regions=request['options']['regions']
    if any(r['page']>len(reader.pages) for r in regions):raise Error('redaction_page_not_found')
    doc=pdfium.PdfDocument(source['content']);doc.init_forms();images=[];pixels=0
    try:
        for number,page in enumerate(doc,1):
            width,height=page.get_size();pixels+=math.ceil(width*2)*math.ceil(height*2)
            if width<=0 or height<=0 or width*height*4>8_000_000 or pixels>40_000_000:raise Error('redaction_pixel_limit')
            bitmap=page.render(scale=2,draw_annots=True);image=bitmap.to_pil().convert('RGB');draw=ImageDraw.Draw(image)
            for region in regions:
                if region['page']==number:
                    draw.rectangle((max(0,math.floor(region['x0']*image.width)-2),max(0,math.floor(region['y0']*image.height)-2),min(image.width,math.ceil(region['x1']*image.width)+2),min(image.height,math.ceil(region['y1']*image.height)+2)),fill=(0,0,0))
            images.append(image);bitmap.close();page.close()
        stream=io.BytesIO();images[0].save(stream,format='PDF',save_all=True,append_images=images[1:],resolution=144,title='',author='',subject='',keywords='',creator='Riwaq raster redaction',producer='')
        raw=stream.getvalue();after=PdfReader(io.BytesIO(raw),strict=True)
        if len(after.pages)!=len(reader.pages) or any(p.extract_text() or p.get('/Annots') for p in after.pages) or any(k in after.trailer['/Root'] for k in ('/AcroForm','/Names','/Metadata')):raise Error('redaction_structure_verification_failed')
        # Rebuild from pixels: no source objects, text layer, forms or attachments are copied.
        verification={'method':'complete-raster-reconstruction','pages':len(after.pages),'text_layer_removed':True,'annotations_forms_attachments_removed':True,'searchable_text':False,'specified_regions':len(regions),'visual_ocr_review_required':True}
        artifact=output_artifact(raw,'riwaq-redacted.pdf',verification,('Every page is rasterized at144dpi. Text search, editable objects, links and forms are removed. Only the explicitly selected regions are redacted; unseen sensitive content elsewhere is not detected automatically.',))
        return verification,[artifact],[_source_ref(source,page=r['page'],region={k:r[k] for k in ('x0','y0','x1','y1')}) for r in regions]
    finally:
        for image in images:image.close()
        doc.close()

def _convert(request,source):
    fmt=request['options']['format'];raw=source['content']
    if raw.startswith(b'PK'):
        records=_records(source);text='\n\n'.join(r['text'] for r in records)
        if fmt=='txt':return {'method':'docx-body-to-utf8','losses':['Formatting, images, headers, footers and printed pagination are not represented.']},[output_artifact(text.encode('utf-8'),'riwaq-converted.txt')],[_source_ref(source,**r['location']) for r in records]
        if fmt=='pdf':
            if request['options'].get('allowReflow') is not True:raise Error('docx_pdf_reflow_consent_required')
            result,outputs,_=_create_pdf({'title':'Document','paragraphs':[r['text'] for r in records if r['text']],'direction':request['options'].get('direction','rtl')},[])
            result.update(method='docx-body-reflow-to-searchable-pdf',losses=['Reflowed body text only. Original Word layout, headers, fields, images and table geometry are not retained.'])
            return result,outputs,[_source_ref(source,**r['location']) for r in records]
        if fmt=='csv':
            data,refs=_tables(source)
            if len(data['tables'])!=1 or any(c['column_span']!=1 or c['vertical_merge'] is not None for c in data['tables'][0]['cells']):raise Error('csv_requires_single_unmerged_table')
            cells=data['tables'][0]['cells'];rows={}
            for cell in cells:
                value=cell['raw'];trimmed=value.lstrip()
                # This conversion preserves literal text. Spreadsheet CSV import
                # may execute formula-looking cells; do not silently rewrite or
                # deliver them without an explicit export policy implemented.
                if trimmed.startswith(('=','@')) or trimmed.startswith(('+','-')) and not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',trimmed):raise Error('csv_formula_prefix_requires_explicit_policy')
                rows.setdefault(cell['row'],[]).append(value)
            stream=io.StringIO(newline='');csv.writer(stream).writerows(rows.values())
            return {'method':'docx-table-to-csv','cell_values':'raw strings; spreadsheet applications may reinterpret values'},[output_artifact(stream.getvalue().encode('utf-8-sig'),'riwaq-converted.csv')],refs
    if raw.startswith(b'%PDF-') and fmt=='txt':
        reader=_pdf(source);pages=[p.extract_text() for p in reader.pages]
        if not any(t.strip() for t in pages):raise Error('pdf_text_layer_missing_no_ocr_conversion')
        return {'method':'pdf-native-text-to-utf8','losses':['No OCR, layout, images or inferred table cells.']},[output_artifact('\n\f\n'.join(pages).encode('utf-8'),'riwaq-converted.txt')],[_source_ref(source,page=n) for n in range(1,len(pages)+1)]
    raise Error('conversion_pair_unsupported')

def execute_documents(request,sources):
    validate_request(request);by_id={s['id']:s for s in sources}
    if set(by_id)!=set(request['sourceIds']) or len(by_id)!=len(sources):raise Error('source_set_mismatch')
    ordered=[by_id[key] for key in request['sourceIds']];op=request['operation']
    if op=='pdf-create':value=_create_pdf(request['options'],ordered)
    elif op in {'pdf-merge','pdf-split'}:value=_merge_split(request,ordered)
    elif op=='pdf-form-fill':value=_fill_form(request,ordered[0])
    elif op=='pdf-redact':value=_redact(request,ordered[0])
    elif op=='docx-edit':value=_edit_docx(request,ordered[0])
    elif op=='docx-compare':value=_compare(request,ordered)
    elif op=='template-fill':value=_template(request,ordered[0])
    elif op=='convert':value=_convert(request,ordered[0])
    elif op=='table-extract':
        result,refs=_tables(ordered[0],request['options'].get('tableIndex'));value=result,[],refs
    else:raise Error('document_operation_unsupported')
    result,artifacts,refs=value
    limitations=list(result.get('limitations',[]))+list(result.get('losses',[]))
    for artifact in artifacts:limitations.extend(artifact.limitations)
    result['limitations']=list(dict.fromkeys(limitations))
    result['status']='partial' if limitations or result.get('appearance_review_required') or result.get('visual_ocr_review_required') else 'completed'
    return result,artifacts,refs
