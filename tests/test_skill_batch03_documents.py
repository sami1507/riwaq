"""Controlled synthetic inputs only; production parsers run in skill_transform."""
import copy,hashlib,io,json,zipfile
from xml.etree import ElementTree as ET
import pytest
from docx import Document
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DictionaryObject,NameObject,TextStringObject,ArrayObject,NumberObject,FloatObject,DecodedStreamObject
from backend.skill_documents import DocumentRequest
from backend.isolated_skill_documents import execute_documents,W,NS
from backend.skill_transform_protocol import TransformError,encode_payload,encode_result,decode_result,output_artifact,loads

def source(raw,key='d1',name='source.docx'):
    return {'id':key,'name':name,'version':1,'sha256':hashlib.sha256(raw).hexdigest(),'content':raw}
def docx(paragraphs=('رقم 15 لا يتغير','Keep independent text'),rows=None):
    doc=Document()
    for p in paragraphs:doc.add_paragraph(p)
    if rows:
        table=doc.add_table(rows=0,cols=len(rows[0]))
        for row in rows:
            for cell,value in zip(table.add_row().cells,row):cell.text=value
    doc.sections[0].header.paragraphs[0].text='Header original'
    buf=io.BytesIO();doc.save(buf);return buf.getvalue()
def rewrite(raw,part,fn):
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as old,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as new:
        for name in old.namelist():new.writestr(name,fn(old.read(name)) if name==part else old.read(name))
    return out.getvalue()
def run(op,options,sources):return execute_documents({'operation':op,'sourceIds':[s['id'] for s in sources],'options':options},sources)
def pdf(widths=(100,200),rotation=0):
    writer=PdfWriter()
    for width in widths:
        p=writer.add_blank_page(width,300)
        if rotation:p.rotate(rotation)
    out=io.BytesIO();writer.write(out);return out.getvalue()
def form():
    writer=PdfWriter();page=writer.add_blank_page(300,300)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    fields=[]
    for index,key in enumerate(('name','keep')):
        field=DictionaryObject({NameObject('/FT'):NameObject('/Tx'),NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Widget'),NameObject('/T'):TextStringObject(key),NameObject('/V'):TextStringObject('original'),NameObject('/DA'):TextStringObject('/Helv 12 Tf 0 g'),NameObject('/Rect'):ArrayObject([FloatObject(v) for v in (20,220-index*50,200,250-index*50)])})
        fields.append(writer._add_object(field))
    page[NameObject('/Annots')]=ArrayObject(fields)
    writer._root_object[NameObject('/AcroForm')]=writer._add_object(DictionaryObject({NameObject('/Fields'):ArrayObject(fields),NameObject('/DR'):DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/Helv'):font})}),NameObject('/DA'):TextStringObject('/Helv 12 Tf 0 g')}))
    out=io.BytesIO();writer.write(out);return out.getvalue()

def test_edit_exact_text_retains_original_unrelated_parts_and_editable_text():
    raw=docx();s=source(raw);_,artifacts,refs=run('docx-edit',{'edits':[{'paragraph':1,'expectedText':'رقم 15 لا يتغير','replacement':'رقم 150 لا يتغير'}]},[s])
    edited=artifacts[0].content;doc=Document(io.BytesIO(edited))
    assert doc.paragraphs[0].text=='رقم 150 لا يتغير';assert doc.paragraphs[1].text=='Keep independent text'
    with zipfile.ZipFile(io.BytesIO(raw)) as a,zipfile.ZipFile(io.BytesIO(edited)) as b:
        assert all(a.read(n)==b.read(n) for n in a.namelist() if n!='word/document.xml')
    assert source(raw)['sha256']==s['sha256'];assert refs[0]['paragraph']==1

def test_edit_rejects_stale_target_and_tracked_changes():
    with pytest.raises(TransformError,match='target_text_changed'):run('docx-edit',{'edits':[{'paragraph':1,'expectedText':'wrong','replacement':'new'}]},[source(docx())])
    raw=rewrite(docx(),'word/document.xml',lambda b:b.replace(b'<w:body>',b'<w:body><w:ins/>'))
    with pytest.raises(TransformError,match='tracked_changes'):run('docx-edit',{'edits':[{'paragraph':1,'expectedText':'wrong','replacement':'new'}]},[source(raw)])

def test_edit_header_and_cell_preserves_body():
    raw=docx(rows=[['001','0'],['','-15']]);_,artifacts,_=run('docx-edit',{'edits':[{'part':'word/header1.xml','paragraph':1,'expectedText':'Header original','replacement':'Header updated'},{'table':1,'row':2,'column':2,'expectedText':'-15','replacement':'-150'}]},[source(raw)])
    doc=Document(io.BytesIO(artifacts[0].content));assert doc.sections[0].header.paragraphs[0].text=='Header updated';assert doc.tables[0].cell(1,1).text=='-150';assert doc.paragraphs[0].text=='رقم 15 لا يتغير'

def test_multiline_edit_keeps_real_word_breaks():
    _,artifacts,_=run('docx-edit',{'edits':[{'paragraph':1,'expectedText':'old','replacement':'new\nline\tend'}]},[source(docx(('old',)))])
    with zipfile.ZipFile(io.BytesIO(artifacts[0].content)) as z:root=ET.fromstring(z.read('word/document.xml'))
    assert root.find('.//w:br',NS) is not None
    assert root.find('.//w:tab',NS) is not None
    assert Document(io.BytesIO(artifacts[0].content)).paragraphs[0].text=='new\nline\tend'

def test_template_literal_no_second_pass_zero_null_repeated_rows():
    raw=docx(('{{name}} {{amount}} {{optional}}',),rows=[['البند','القيمة'],['{{items.name}}','{{items.value}}']])
    result,artifacts,_=run('template-fill',{'values':{'name':'{{secret}}','amount':0,'optional':None},'optionalFields':['optional'],'repeatRows':{'items':[{'name':'أ','value':0},{'name':'ב','value':-15}]}},[source(raw)])
    doc=Document(io.BytesIO(artifacts[0].content));assert doc.paragraphs[0].text=='{{secret}} 0 ';assert len(doc.tables[0].rows)==3
    assert [[c.text for c in r.cells] for r in doc.tables[0].rows][1:]==[['أ','0'],['ב','-15']]

@pytest.mark.parametrize('options,code',[({'values':{}},'required_field_missing'),({'values':{'name':None}},'required_template_null'),({'values':{'name':'x','unused':'x'}},'supplied_field_not_found')])
def test_template_required_missing_null_unused(options,code):
    with pytest.raises(TransformError,match=code):run('template-fill',options,[source(docx(('{{name}}',)))])

def test_comparison_flags_numbers_negation_and_locations():
    result,artifacts,refs=run('docx-compare',{},[source(docx(('لا تدفع 15',)),'old'),source(docx(('ادفع 150',)),'new')])
    assert result['changes'][0]['numeric_literals_changed'];assert result['changes'][0]['negation_markers_changed'];assert refs[0]['source_id']=='old';assert not artifacts

def test_table_raw_zero_blank_leading_zero_negative():
    result,_,refs=run('table-extract',{},[source(docx((),rows=[['الرمز','القيمة'],['001','0'],['','-15']]))])
    cells=result['tables'][0]['cells'];assert [c['raw'] for c in cells]==['الرمز','القيمة','001','0','','-15'];assert all(c['interpretation']=='text' for c in cells);assert refs[0]['table']==1

def test_merge_split_order_geometry_rotation_and_explicit_repeat():
    s=source(pdf(rotation=90),name='a.pdf');r,arts,refs=run('pdf-merge',{'selections':[{'sourceId':'d1','pages':[2,1]}]},[s]);reader=PdfReader(io.BytesIO(arts[0].content))
    assert [float(p.mediabox.width) for p in reader.pages]==[200,100];assert all(p.rotation==90 for p in reader.pages)
    with pytest.raises(TransformError,match='repeated_pages'):run('pdf-merge',{'selections':[{'sourceId':'d1','pages':[1,1]}]},[s])
    _,arts,_=run('pdf-split',{'selections':[{'sourceId':'d1','pages':[1]},{'sourceId':'d1','pages':[2]}]},[s]);assert len(arts)==2

def test_form_values_verified_preserves_untargeted_and_blocks_nonascii():
    s=source(form(),name='form.pdf');result,arts,_=run('pdf-form-fill',{'values':{'name':'Test 15'}},[s]);fields=PdfReader(io.BytesIO(arts[0].content)).get_fields()
    assert fields['name']['/V']=='Test 15';assert fields['keep']['/V']=='original';assert result['values_verified']
    with pytest.raises(TransformError,match='unicode_appearance'):run('pdf-form-fill',{'values':{'name':'اسم'}},[s])
    with pytest.raises(TransformError,match='forms_require'):run('pdf-merge',{'selections':[{'sourceId':'d1','pages':[1]}]},[s])

def test_no_signature_or_active_pdf_bypass():
    writer=PdfWriter();writer.add_blank_page(100,100);writer._root_object[NameObject('/OpenAction')]=DictionaryObject({NameObject('/S'):NameObject('/JavaScript'),NameObject('/JS'):TextStringObject('bad')});buf=io.BytesIO();writer.write(buf)
    with pytest.raises(TransformError,match='active_or_embedded'):run('pdf-merge',{'selections':[{'sourceId':'d1','pages':[1]}]},[source(buf.getvalue(),name='bad.pdf')])

def test_passive_page_view_destination_is_not_executable_action():
    writer=PdfWriter();page=writer.add_blank_page(100,100);writer._root_object[NameObject('/OpenAction')]=ArrayObject([page.indirect_reference,NameObject('/FitH'),NumberObject(0)])
    buf=io.BytesIO();writer.write(buf)
    _,arts,_=run('pdf-merge',{'selections':[{'sourceId':'d1','pages':[1]}]},[source(buf.getvalue(),name='passive.pdf')])
    assert len(PdfReader(io.BytesIO(arts[0].content)).pages)==1

def test_checkbox_values_use_actual_appearance_states():
    reader=PdfReader(io.BytesIO(form()));writer=PdfWriter();writer.clone_document_from_reader(reader)
    widget=writer.pages[0]['/Annots'][0].get_object();widget[NameObject('/FT')]=NameObject('/Btn');widget[NameObject('/V')]=NameObject('/Off')
    appearances={}
    for state,paint in [('/Off',b''),('/Yes',b'0 0 1 rg 0 0 20 20 re f')]:
        stream=DecodedStreamObject();stream.set_data(paint);stream[NameObject('/BBox')]=ArrayObject([NumberObject(x) for x in (0,0,20,20)]);appearances[NameObject(state)]=writer._add_object(stream)
    widget[NameObject('/AP')]=DictionaryObject({NameObject('/N'):DictionaryObject(appearances)});widget[NameObject('/AS')]=NameObject('/Off')
    buf=io.BytesIO();writer.write(buf);result,arts,_=run('pdf-form-fill',{'values':{'name':True}},[source(buf.getvalue(),name='checkbox.pdf')]);r=PdfReader(io.BytesIO(arts[0].content))
    assert r.get_fields()['name']['/V']=='/Yes';assert r.pages[0]['/Annots'][0].get_object()['/AS']=='/Yes'

def test_conversion_not_extension_rename_and_reflow_requires_consent():
    s=source(docx(('Hello 001',)));_,a,_=run('convert',{'format':'txt'},[s]);assert a[0].content==b'Hello 001'
    with pytest.raises(TransformError,match='reflow'):run('convert',{'format':'pdf'},[s])
    with pytest.raises(TransformError,match='unsupported'):run('convert',{'format':'csv'},[source(pdf(),name='x.pdf')])

def test_docx_csv_rejects_formula_interpretation_instead_of_silent_execution():
    with pytest.raises(TransformError,match='csv_formula_prefix_requires_explicit_policy'):
        run('convert',{'format':'csv'},[source(docx((),rows=[['item','value'],['x','=WEBSERVICE("https://invalid.example")']]))])

def test_template_extra_repeat_field_is_not_silently_dropped():
    with pytest.raises(TransformError,match='template_repeat_field_not_found'):
        run('template-fill',{'values':{},'repeatRows':{'items':[{'name':'a','amount':15}]}},[source(docx((),rows=[['{{items.name}}']]))])

@pytest.mark.parametrize('options',[{'regions':[{'page':1,'x0':0,'y0':0,'x1':1,'y1':1}],'allowRasterization':False},{'regions':[{'page':1,'x0':float('nan'),'y0':0,'x1':1,'y1':1}],'allowRasterization':True}])
def test_redaction_requires_explicit_raster_and_finite_coordinates(options):
    with pytest.raises(ValueError):DocumentRequest.model_validate({'operation':'pdf-redact','sourceIds':['a'],'options':options})

def test_wire_binds_input_hash_output_and_reference_version():
    s=source(b'not parsed');payload=encode_payload('documents',{'operation':'x'},[s]);artifact=output_artifact(b'abc','result.txt');ref={'source_id':'d1','sha256':s['sha256'],'version':1}
    raw=encode_result(payload,{'ok':True},[artifact],[ref]);assert decode_result(raw,payload,{}).artifacts[0].content==b'abc'
    with pytest.raises(TransformError,match='identity'):decode_result(raw,payload+b' ',{})
    data=loads(raw);data['source_refs'][0]['version']=2
    with pytest.raises(TransformError,match='version'):decode_result(json.dumps(data).encode(),payload,{})
    s['sha256']='0'*64
    with pytest.raises(TransformError,match='hash'):encode_payload('documents',{},[s])

def test_no_host_fallback_and_revoke_before_payload(monkeypatch):
    from backend import skill_transform
    monkeypatch.setattr(skill_transform,'execute',lambda *a,**k:pytest.fail('must not execute'))
    with pytest.raises(TransformError,match='authority_revoked'):skill_transform.execute_transform('documents',{},[],check=lambda:False)
    with pytest.raises(TransformError,match='authority_required'):skill_transform.execute_transform('documents',{},[],check=None)

@pytest.mark.parametrize('event,code',[('cancel','transform_cancelled'),('revoke','authority_revoked')])
def test_late_worker_result_is_withheld_after_cancel_or_revocation(monkeypatch,event,code):
    from backend import skill_transform
    from contextlib import nullcontext
    from types import SimpleNamespace
    import threading
    stop=threading.Event();permitted=[True]
    monkeypatch.setattr(skill_transform,'worker_lease',lambda:nullcontext())
    monkeypatch.setattr(skill_transform,'load_config',lambda path:SimpleNamespace(images={'skill_transform':'sha256:'+'a'*64}))
    monkeypatch.setattr(skill_transform,'assert_ready',lambda:None)
    monkeypatch.setattr(skill_transform,'Journal',lambda *a:SimpleNamespace(event=lambda *a:None,finish=lambda *a:None))
    def fake(*args,**kwargs):
        if event=='cancel':stop.set()
        else:permitted[0]=False
        return {'status':'completed','cleanup_verified':True,'policy_inspected':True,'exit_code':0,'stdout':'untrusted output must never reach decoder','stderr':''}
    monkeypatch.setattr(skill_transform,'execute',fake)
    with pytest.raises(TransformError,match=code):skill_transform.execute_transform('documents',{},[],check=lambda:permitted[0],stop=stop)

def test_edit_keeps_namespace_declarations_for_compatibility_attributes():
    raw=docx(('old',));_,arts,_=run('docx-edit',{'edits':[{'paragraph':1,'expectedText':'old','replacement':'new'}]},[source(raw)])
    with zipfile.ZipFile(io.BytesIO(arts[0].content)) as z:
        data=z.read('word/document.xml');decls=dict(pair for _,pair in ET.iterparse(io.BytesIO(data),events=['start-ns']))
        root=ET.fromstring(data);ignored=root.get('{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable','').split()
        assert all(prefix in decls for prefix in ignored)

def test_actual_isolated_document_workflow(tmp_path,monkeypatch):
    import os,pathlib
    if os.environ.get('RIWAQ_TEST_BATCH03_DOCUMENTS')!='1':pytest.skip('Opt-in actual Docker renderer, no host parser fallback')
    from backend.skill_documents import run_document_operation
    from backend.skill_transform import execute_transform
    from backend import document_isolation
    report=pathlib.Path('reports/skills-batch-03-20261116/documents/live');report.mkdir(parents=True,exist_ok=True)
    cfg=pathlib.Path('reports/skills-batch-03-20261116/documents/isolated-workers.json').read_bytes();(tmp_path/'isolated-workers.json').write_bytes(cfg)
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    receipts=[]
    def operation(label,op,options,sources):
        result=run_document_operation({'operation':op,'sourceIds':[s['id'] for s in sources],'options':options},sources,check=lambda:True)
        receipts.append({'label':label,'result':result.result,'receipt':result.receipt,'artifacts':[{'name':a.filename,'sha256':a.sha256,'verification':a.verification,'limitations':a.limitations} for a in result.artifacts]})
        (report/'receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding='utf-8')
        for artifact in result.artifacts:(report/(label+'-'+artifact.filename)).write_bytes(artifact.content)
        assert result.receipt['cleanup_verified'] and result.receipt['policy_inspected'] and result.receipt['exit_code']==0
        return result
    options={'title':'رواق Riwaq 2026','paragraphs':['المبلغ 15 وليس 150. لا تدفع الآن.','הסכום 15 ולא 150.','English amount 15, not 150.'],'direction':'rtl','tables':[{'headers':['الرمز','القيمة'], 'rows':[[str(i),'0' if i%2==0 else '-15'] for i in range(1,81)]}]}
    created=operation('created','pdf-create',options,[]);assert created.result['searchable_text'];assert created.result['pages']>=2
    for page in range(1,created.result['pages']+1):
        result,preview,receipt=document_isolation.extract(created.artifacts[0].content,'created.pdf',page,'ara+heb+eng',authorize=lambda:True)
        (report/f'created-page-{page}.png').write_bytes(preview);assert receipt['cleanup_verified']
    original=source(docx(('{{name}} 15 لا تدفع',),rows=[['Item','Value'],['{{items.name}}','{{items.value}}']]))
    populated=operation('populated','template-fill',{'values':{'name':'رواق'},'repeatRows':{'items':[{'name':'001','value':0},{'name':'ב','value':-15}]}},[original])
    derived=source(populated.artifacts[0].content,'derived')
    edited=operation('edited','docx-edit',{'edits':[{'paragraph':1,'expectedText':'رواق 15 لا تدفع','replacement':'رواق 150 لا تدفع'}]},[derived])
    compared=operation('compared','docx-compare',{},[derived,source(edited.artifacts[0].content,'new')]);assert compared.result['changes'][0]['numeric_literals_changed']
    operation('reflow','convert',{'format':'pdf','allowReflow':True},[source(edited.artifacts[0].content)])
    filled=operation('form','pdf-form-fill',{'values':{'name':'Test 15'}},[source(form(),name='form.pdf')])
    result,preview,receipt=document_isolation.extract(filled.artifacts[0].content,'filled.pdf',1,'eng',authorize=lambda:True)
    (report/'form-page-1.png').write_bytes(preview)
    merged=operation('merged','pdf-merge',{'selections':[{'sourceId':'d1','pages':[2,1]}]},[source(created.artifacts[0].content,name='created.pdf')]);assert merged.result
    data=execute_transform('data',{'operation':'stats','table':{'columns':['amount'],'rows':[[10],[0],[20],[None]]},'columns':['amount']},[],check=lambda:True)
    (report/'data-stats.json').write_text(json.dumps({'result':data.result,'receipt':data.receipt},indent=2),encoding='utf-8')
    assert data.result['summary']['statistics']['amount']['sum']=='30'
    # Synthetic secret exists in page text, raster image, annotation and metadata.
    canary='CANARY REMOVE 839271'
    from PIL import Image,ImageDraw,ImageFont
    img=Image.new('RGB',(420,100),'white');ImageDraw.Draw(img).text((8,25),canary,fill='black',font=ImageFont.load_default(size=20));png=io.BytesIO();img.save(png,format='PNG')
    secret=operation('secret','pdf-create',{'title':'Safe heading','paragraphs':[canary]+['Public safe line 42']*5+['KEEP PUBLIC 42'],'direction':'ltr','images':[{'sourceId':'raster','caption':'Image example'}]},[source(png.getvalue(),'raster','example.png')])
    reader=PdfReader(io.BytesIO(secret.artifacts[0].content));writer=PdfWriter();writer.clone_document_from_reader(reader);writer.add_metadata({'/Title':canary,'/Subject':canary});writer.add_attachment('private.txt',canary.encode())
    annotation=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Text'),NameObject('/Contents'):TextStringObject(canary),NameObject('/Rect'):ArrayObject([NumberObject(x) for x in (10,700,20,710)])})
    writer.pages[0][NameObject('/Annots')]=ArrayObject([writer._add_object(annotation)]);b=io.BytesIO();writer.write(b)
    redacted=operation('redacted','pdf-redact',{'allowRasterization':True,'regions':[{'page':1,'x0':0,'y0':0,'x1':1,'y1':.20},{'page':1,'x0':0,'y0':.36,'x1':1,'y1':1}]},[source(b.getvalue(),name='secret.pdf')])
    raw=redacted.artifacts[0].content;r=PdfReader(io.BytesIO(raw));assert not r.pages[0].extract_text();assert canary not in str(r.metadata);assert canary.encode() not in raw
    result,preview,receipt=document_isolation.extract(raw,'redacted.pdf',1,'eng',authorize=lambda:True)
    (report/'redacted-page-1.png').write_bytes(preview);(report/'redaction-ocr.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert canary not in json.dumps(result);assert '839271' not in json.dumps(result)
    assert 'KEEP PUBLIC 42' in result['page']['ocr']['text_raw']

def test_actual_acroform_preview(tmp_path,monkeypatch):
    import os,pathlib
    if os.environ.get('RIWAQ_TEST_BATCH03_DOCUMENTS')!='1':pytest.skip('Opt-in actual isolated PDFium/OCR')
    from backend import document_isolation
    base=pathlib.Path('reports/skills-batch-03-20261116/documents');(tmp_path/'isolated-workers.json').write_bytes((base/'isolated-workers.json').read_bytes());monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    raw=(base/'live/form-riwaq-filled.pdf').read_bytes()
    result,preview,receipt=document_isolation.extract(raw,'filled.pdf',1,'eng',authorize=lambda:True)
    (base/'live/form-page-1.png').write_bytes(preview)
    (base/'live/form-preview-ocr.json').write_text(json.dumps({'result':result,'receipt':receipt},indent=2),encoding='utf-8')
    assert 'Test 15' in result['page']['ocr']['text_raw']
