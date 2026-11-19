"""Parser security, exact source values and physical positions; no DB or API."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import zlib

import pytest
from PIL import Image
from backend import document_extract as de


def archive(parts, compression=zipfile.ZIP_STORED):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=compression) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        for name,value in parts.items():z.writestr(name,value)
    return output.getvalue()


def run(tmp_path, data, name, page=1, **kwargs):
    source=tmp_path/'input.bin';source.write_bytes(data)
    return de.extract_document(source,name,tmp_path/'output',page,**kwargs)


def tsv(words):
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    return header+''.join('5\t1\t1\t1\t1\t%d\t%d\t%d\t%d\t%d\t%s\t%s\n'%(index,*word)
                          for index,word in enumerate(words,1))


def test_utf8_text_raw_preserves_crlf_blanks_combining_and_digits(tmp_path):
    text='\u0627\u0644\u0642\u064a\u0645\u0629 001\r\n\r\nCafe\u0301 \u05e9\u05dc\u05d5\u05dd\r\n'
    result=run(tmp_path,text.encode(),'note.md')
    item=result['page']['elements'][0]
    assert item['text_raw']==item['text']==text
    assert item['locator']['lines']==[1,3] and item['bbox'] is None
    assert result['metadata']['text_normalization']=='none'
    assert result['page']['preview_file'] is None


def test_csv_zeros_multiline_empty_fields_and_blank_rows_are_not_inferred_numbers(tmp_path):
    raw='id,value,empty\r\n001,0,\r\n\r\n002,"line1\nline2",0004\r\n'
    result=run(tmp_path,raw.encode(),'data.csv')['page']['elements'][0]
    assert result['text_raw']==raw
    values={c['address']:c for c in result['cells']}
    assert values['A2']['value']=='001' and values['B2']['value']=='0'
    assert values['C2']['kind']=='blank' and values['C2']['value']==''
    assert values['B4']['value']=='line1\nline2' and values['C4']['value']=='0004'
    assert result['row_lengths']==[3,3,0,3]
    assert result['source_lines']==[[1,1],[2,2],[3,3],[4,5]]
    assert all(c['formula'] is None and c['cached_value'] is None for c in result['cells'])


@pytest.mark.parametrize('data,name', [(b'%PDF-fake','fake.txt'),(b'not an image','fake.png'),(b'\xff','bad.txt'),(b'x\x00y','binary.txt')])
def test_invalid_actual_types_rejected(tmp_path,data,name):
    with pytest.raises(de.DocumentError):run(tmp_path,data,name,0)


@pytest.mark.parametrize('name',['../x.txt','C:\\x.txt','x\x00.txt'])
def test_original_name_does_not_select_path(tmp_path,name):
    with pytest.raises(de.DocumentError):run(tmp_path,b'safe',name)


def test_size_page_and_structured_byte_limits(tmp_path):
    with pytest.raises(de.DocumentError):run(tmp_path,b'12345','x.txt',limits=de.Limits(max_input_bytes=4))
    with pytest.raises(de.DocumentError):run(tmp_path,b'x','x.txt',page=2)
    with pytest.raises(de.DocumentError):run(tmp_path,b'x'*200,'x.txt',limits=de.Limits(max_extracted_bytes=250))


def test_limits_config_can_only_reduce_known_bounds(tmp_path):
    path=tmp_path/'limits.json';path.write_text('{"max_input_bytes":100}')
    assert de.load_limits(path).max_input_bytes==100
    for bad in [{'max_pages':201},{'unexpected':1},{'max_pages':True},{'max_cells':0}]:
        path.write_text(json.dumps(bad))
        with pytest.raises(de.DocumentError):de.load_limits(path)


def test_docx_structure_and_table_strings_no_fake_page_layout(tmp_path):
    doc='<w:document xmlns:w="%s"><w:body><w:p><w:r><w:t xml:space="preserve"> A </w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>001</w:t></w:r></w:p></w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl></w:body></w:document>'%de.NS['w']
    data=archive({'word/document.xml':doc})
    inspect=run(tmp_path,data,'body.docx',0)
    assert inspect['page_count']==1 and inspect['metadata']['page_units']=='logical_document_body'
    result=run(tmp_path,data,'body.docx')['page']
    assert result['elements'][0]['text_raw']==' A \tB\nC'
    table=result['elements'][1]
    assert table['locator']=={'page':1,'table':1}
    assert table['cells'][0]['value']=='001' and table['cells'][0]['colspan']==2
    assert table['cells'][1]['column']==3 and table['cells'][1]['value']==''
    assert all(item['bbox'] is None for item in result['elements'])


def spreadsheet():
    book='<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="Values" sheetId="1" r:id="r1"/></sheets></workbook>'%(de.NS['s'],de.NS['r'])
    rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>'
    sheet='<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>001</t></is></c><c r="B1"><v>0</v></c><c r="C1"/><c r="D1"><f>SUM(B1,2)</f><v>2</v></c><c r="E1"><f t="shared" si="4"/></c><c r="F1" s="2"><v>45000</v></c></row></sheetData><mergeCells><mergeCell ref="A2:B2"/></mergeCells></worksheet>'%de.NS['s']
    return archive({'xl/workbook.xml':book,'xl/_rels/workbook.xml.rels':rel,'xl/worksheets/sheet1.xml':sheet})


def test_xlsx_raw_formula_cached_values_blanks_merges_no_execution(tmp_path):
    result=run(tmp_path,spreadsheet(),'values.xlsx')['page']
    item=result['elements'][0];cells={x['address']:x for x in item['cells']}
    assert cells['A1']['value']=='001' and cells['B1']['value']=='0'
    assert cells['C1']['kind']=='blank' and cells['C1']['value']==''
    assert cells['D1']['formula']=='SUM(B1,2)' and cells['D1']['cached_value']=='2'
    assert cells['E1']['formula']=='' and cells['E1']['cached_value'] is None
    assert cells['E1']['formula_type']=='shared'
    assert cells['F1']['value']=='45000' and cells['F1']['style_index']=='2'
    assert item['merged_ranges']==['A2:B2'] and item['locator']['sheet']=='Values'
    assert result['kind']=='sheet' and result['preview_file'] is None


def test_pptx_slide_order_and_text_locations(tmp_path):
    presentation='<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst><p:sldId id="256" r:id="a"/></p:sldIdLst></p:presentation>'%(de.NS['p'],de.NS['r'])
    rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="a" Target="slides/slide1.xml"/></Relationships>'
    slide='<p:sld xmlns:p="%s" xmlns:a="%s"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Exact slide</a:t></a:r><a:br/><a:r><a:t>002</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'%(de.NS['p'],de.NS['a'])
    result=run(tmp_path,archive({'ppt/presentation.xml':presentation,'ppt/_rels/presentation.xml.rels':rel,'ppt/slides/slide1.xml':slide}),'slides.pptx')['page']
    assert result['kind']=='slide' and result['elements'][0]['text_raw']=='Exact slide\n002'
    assert result['elements'][0]['locator']=={'page':1,'slide':1,'shape':1}


@pytest.mark.parametrize('evil', ['../escape.xml','C:/escape.xml','word\\escape.xml','word/vbaProject.bin'])
def test_zip_traversal_and_macros_never_extract(tmp_path,evil):
    data=archive({'word/document.xml':'<document/>',evil:'x'})
    if '\\' in evil:
        # ZipFile normalizes backslashes on Windows; encode an actual hostile member.
        data=data.replace(b'word/escape.xml',b'word\\escape.xml')
    with pytest.raises(de.DocumentError):run(tmp_path,data,'evil.docx',0)
    assert not (tmp_path/'escape.xml').exists()


def test_zip_bomb_entry_count_and_duplicate_names(tmp_path):
    bomb=archive({'word/document.xml':'<document>'+('x'*100000)+'</document>'},zipfile.ZIP_DEFLATED)
    with pytest.raises(de.DocumentError):run(tmp_path,bomb,'bomb.docx',0)
    data=archive({'word/document.xml':'<document/>','extra.txt':'x'})
    with pytest.raises(de.DocumentError):run(tmp_path,data,'many.docx',0,limits=de.Limits(max_zip_entries=2))
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('same','a')
        with pytest.warns(UserWarning):z.writestr('same','b')
    with pytest.raises(de.DocumentError):run(tmp_path,out.getvalue(),'duplicate.docx',0)


@pytest.mark.parametrize('encoding',['utf-8','utf-16'])
def test_xml_entities_and_utf16_bypass_rejected(tmp_path,encoding):
    evil='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY probe SYSTEM "file:///etc/passwd">]><x>&probe;</x>'
    data=archive({'word/document.xml':evil.encode(encoding)})
    with pytest.raises(de.DocumentError):run(tmp_path,data,'entity.docx',0)


def test_external_relationship_not_fetched(tmp_path):
    data=archive({'word/document.xml':'<w:document xmlns:w="%s"><w:body/></w:document>'%de.NS['w'],
                  'word/_rels/document.xml.rels':'<Relationships><Relationship Id="r1" TargetMode="External" Target="https://example.invalid/private"/></Relationships>'})
    result=run(tmp_path,data,'link.docx')
    assert result['success'] and result['metadata']['source_network'] is False
    assert result['page']['elements']==[]


def image_bytes(size=(80,40),format='PNG',orientation=None):
    image=Image.new('RGB',size,'white');out=io.BytesIO()
    if orientation:
        exif=Image.Exif();exif[274]=orientation;image.save(out,format=format,exif=exif)
    else:image.save(out,format=format)
    return out.getvalue()


def test_actual_image_type_exif_normalization_and_missing_ocr_are_honest(tmp_path,monkeypatch):
    monkeypatch.setattr(de,'run_ocr',lambda *args:{'success':False,'warnings':['OCR unavailable']})
    data=image_bytes((80,40),'JPEG',6)
    result=run(tmp_path,data,'claimed.txt')
    assert result['mime']=='image/jpeg'
    page=result['page'];assert (page['width'],page['height'])==(40,80)
    assert page['status']=='failed' and page['elements']==[]
    assert page['preview_file']=='page.png'
    with Image.open(tmp_path/'output'/'page.png') as image:assert not image.getexif()
    assert (tmp_path/'input.bin').read_bytes()==data


def test_image_pixel_bomb_and_animation_rejected(tmp_path):
    with pytest.raises(de.DocumentError):run(tmp_path,image_bytes((20,20)),'x.png',0,limits=de.Limits(max_pixels=399))
    output=io.BytesIO();Image.new('RGB',(10,10),'red').save(output,format='PNG',save_all=True,append_images=[Image.new('RGB',(10,10),'blue')],duration=100)
    with pytest.raises(de.DocumentError):run(tmp_path,output.getvalue(),'animated.png',0)


def test_tsv_word_positions_low_confidence_and_native_overlap():
    data=tsv([(0,0,20,10,99,'native'),(30,20,10,10,95,'clear'),(50,20,10,10,12,'guess')])
    items,suppressed=de.parse_tsv(data,2,100,100,de.Limits(),[[0,0,.2,.1]])
    assert suppressed==1 and len(items)==1
    item=items[0]
    assert item['text_raw']=='clear guess' and item['text']=='clear [unreadable]'
    assert item['bbox']==[.3,.2,.6,.3] and item['locator']['page']==2
    assert item['quality']['readable'] is False and item['quality']['low_confidence_words']==1
    assert 'not probability' in item['quality']['confidence_scale']


def test_tsv_fabricated_coordinates_rejected():
    with pytest.raises(de.DocumentError):de.parse_tsv(tsv([(90,0,30,10,90,'bad')]),1,100,100,de.Limits())


@pytest.fixture
def pdfium(monkeypatch):
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))/'python-packages'
    if runtime.is_dir():monkeypatch.syspath_prepend(str(runtime))
    return pytest.importorskip('pypdfium2',reason='Local PDFium runtime is not installed yet')


def pdf_bytes(image=False):
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>']
    resources=b'<< /Font << /F1 5 0 R >>'+(b' /XObject << /I1 6 0 R >>' if image else b'')+b' >>'
    objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources '+resources+b' /Contents 4 0 R >>')
    content=b'BT /F1 12 Tf 20 170 Td (Native source 42) Tj ET'+(b' q 100 0 0 100 50 20 cm /I1 Do Q' if image else b'')
    objects.extend([b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'])
    if image:
        pixels=zlib.compress(bytes([255,255,255])*100)
        objects.append(b'<< /Type /XObject /Subtype /Image /Width 10 /Height 10 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length '+str(len(pixels)).encode()+b' >>\nstream\n'+pixels+b'\nendstream')
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=b'xref\n0 '+str(len(objects)+1).encode()+b'\n0000000000 65535 f \n'
    data+=b''.join(('%010d 00000 n \n'%offset).encode() for offset in offsets[1:])
    return data+b'trailer\n<< /Size '+str(len(objects)+1).encode()+b' /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'


def test_real_pdfium_native_text_bounds_preview_and_no_duplicate_ocr(tmp_path,monkeypatch,pdfium):
    def forbidden(*args):pytest.fail('Native PDF text must not be OCRed')
    monkeypatch.setattr(de,'run_ocr',forbidden)
    inspected=run(tmp_path,pdf_bytes(),'source.pdf',0)
    assert inspected['page_count']==1 and inspected['metadata']['printed_page_number'] is None
    result=run(tmp_path,pdf_bytes(),'source.pdf')['page']
    assert result['kind']=='native' and result['status']=='complete'
    text=next(e for e in result['elements'] if 'Native source 42' in e['text_raw'])
    assert text['bbox'] and all(0<=v<=1 for v in text['bbox'])
    assert .08<text['bbox'][0]<.12 and .05<text['bbox'][1]<.2
    assert (tmp_path/'output'/'page.png').is_file()
    again=run(tmp_path,pdf_bytes(),'source.pdf')['page']
    assert again['elements']==result['elements']


def test_real_pdfium_mixed_page_tracks_images_and_mock_ocr_failure(tmp_path,monkeypatch,pdfium):
    monkeypatch.setattr(de,'run_ocr',lambda *args:{'success':False,'warnings':['simulated OCR unavailable']})
    result=run(tmp_path,pdf_bytes(True),'mixed.pdf')['page']
    assert result['kind']=='mixed' and result['status']=='partial'
    assert any(e['type']=='image' and e['bbox'] for e in result['elements'])
    assert any('Native source 42' in e['text_raw'] for e in result['elements'])


def test_fixed_worker_cli_inspect_extract_and_rejects_invalid_input(tmp_path):
    source=tmp_path/'source.txt';source.write_bytes(b'001\r\n\r\nvalue')
    script=Path(__file__).resolve().parents[1]/'scripts'/'document_worker.py'
    for number in (0,1):
        output=tmp_path/('page'+str(number))
        completed=subprocess.run([sys.executable,'-X','utf8',str(script),'--input',str(source),'--name','source.txt',
            '--output',str(output),'--page',str(number),'--languages','ara+eng+heb'],capture_output=True,timeout=10)
        assert completed.returncode==0,completed.stderr.decode(errors='replace')
        result=json.loads((output/'result.json').read_text(encoding='utf-8'))
        assert result['success'] and result['mime']=='text/plain' and result['page_count']==1
        if number:assert result['page']['elements'][0]['text_raw']=='001\r\n\r\nvalue'
    output=tmp_path/'invalid'
    completed=subprocess.run([sys.executable,str(script),'--input',str(source),'--name','source.txt','--output',str(output),
        '--page','1','--languages','eng+../evil'],capture_output=True,timeout=10)
    assert completed.returncode==1 and json.loads((output/'result.json').read_text())['success'] is False



def test_fixed_worker_python_network_guard_blocks_dns_and_sockets():
    script=Path(__file__).resolve().parents[1]/'scripts'/'document_worker.py'
    probe="import runpy,socket;runpy.run_path(%r);blocked=0\nfor call in (lambda:socket.socket(),lambda:socket.getaddrinfo('example.invalid',443)):\n try:call()\n except OSError as exc:\n  assert 'disabled' in str(exc);blocked+=1\nassert blocked==2"%str(script)
    completed=subprocess.run([sys.executable,'-B','-c',probe],capture_output=True,timeout=10)
    assert completed.returncode==0,completed.stderr.decode(errors='replace')



def test_real_openpyxl_package_root_relationship_and_exact_values(tmp_path, monkeypatch):
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))/'python-packages'
    if runtime.is_dir():monkeypatch.syspath_prepend(str(runtime))
    openpyxl=pytest.importorskip('openpyxl')
    book=openpyxl.Workbook();sheet=book.active;sheet.title='Real sheet'
    sheet['A1']='001';sheet['B1']=0;sheet['C1']='=SUM(B1,2)';sheet.merge_cells('A3:B3')
    data=io.BytesIO();book.save(data);book.close()
    result=run(tmp_path,data.getvalue(),'generated.xlsx')['page']['elements'][0]
    cells={item['address']:item for item in result['cells']}
    assert cells['A1']['value']=='001' and cells['B1']['value']=='0'
    assert cells['C1']['formula']=='SUM(B1,2)' and cells['C1']['cached_value'] is None
    assert result['merged_ranges']==['A3:B3']


def test_negative_shared_string_reference_cannot_alias_last_string(tmp_path):
    original=zipfile.ZipFile(io.BytesIO(spreadsheet()))
    parts={name:original.read(name) for name in original.namelist() if name!='[Content_Types].xml'}
    parts['xl/sharedStrings.xml']='<sst xmlns="%s"><si><t>DO NOT ALIAS</t></si></sst>'%de.NS['s']
    parts['xl/worksheets/sheet1.xml']=('<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="s"><v>-1</v></c></row></sheetData></worksheet>'%de.NS['s']).encode()
    with pytest.raises(de.DocumentError):run(tmp_path,archive(parts),'negative.xlsx')



def test_boxes_outside_preview_cannot_become_fake_zero_area_regions():
    assert de.normalized_box([-20,5,-10,10],100,100) is None
    assert de.normalized_box([105,5,110,10],100,100) is None
    assert de.normalized_box([-10,5,10,10],100,100)==[0,.05,.1,.1]


def test_real_pdfium_rotated_native_bbox_matches_preview_ink(tmp_path,monkeypatch,pdfium):
    monkeypatch.setattr(de,'run_ocr',lambda *args:pytest.fail('Native rotated PDF must not need OCR'))
    data=pdf_bytes().replace(b'/MediaBox [0 0 200 200]',b'/Rotate 90 /MediaBox [0 0 200 200]')
    result=run(tmp_path,data,'rotated.pdf')['page']
    item=next(e for e in result['elements'] if 'Native source 42' in e['text_raw'])
    x0,y0,x1,y1=item['bbox']
    assert x0>.7 and y0<.2
    with Image.open(tmp_path/'output'/'page.png') as image:
        crop=image.crop((int(x0*image.width),int(y0*image.height),int(x1*image.width)+1,int(y1*image.height)+1)).convert('L')
        assert crop.getextrema()[0]<80



def test_real_local_ocr_english_controlled_image(tmp_path,monkeypatch):
    from PIL import ImageDraw, ImageFont
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))
    font_path=Path('C:/Windows/Fonts/arial.ttf')
    if not (runtime/'runtime-manifest.json').is_file() or not font_path.is_file():
        pytest.skip('Configured local OCR runtime and controlled fixture font are required')
    monkeypatch.setenv('RIWAQ_DOCUMENT_RUNTIME',str(runtime))
    picture=Image.new('RGB',(1100,180),'white')
    ImageDraw.Draw(picture).text((40,55),'The project contains 12 files.',font=ImageFont.truetype(str(font_path),48),fill='black')
    data=io.BytesIO();picture.save(data,format='PNG')
    result=run(tmp_path,data.getvalue(),'english-control.png',language='eng')['page']
    raw=' '.join(item['text_raw'] for item in result['elements'])
    assert 'project' in raw.lower() and '12' in raw, result
    assert result['ocr']['engine']=='tesseract.js'
    assert result['ocr']['tsv_sha256'] and all(item['bbox'] for item in result['elements'])
    assert all(item['quality']['review_required'] for item in result['elements'])



def test_api_tsv_without_cli_header_preserves_word_records():
    with_header=tsv([(5,6,20,10,93,'API')])
    without_header=with_header.split('\n',1)[1]
    before=without_header
    assert de.parse_tsv(without_header,1,100,100,de.Limits())==de.parse_tsv(with_header,1,100,100,de.Limits())
    assert without_header==before



@pytest.mark.parametrize('delimiter',[';','\t'])
def test_csv_detects_semicolon_and_tab_preserving_zeros_and_blanks(tmp_path,delimiter):
    raw=delimiter.join(['id','amount','blank'])+'\r\n'+delimiter.join(['001','0',''])+'\r\n'+delimiter.join(['002','1,25','000'])+'\r\n'
    item=run(tmp_path,raw.encode(),'regional.csv')['page']['elements'][0]
    cells={value['address']:value for value in item['cells']}
    assert item['delimiter']==delimiter and item['text_raw']==raw
    assert cells['A2']['value']=='001' and cells['B2']['value']=='0' and cells['C2']['value']==''
    assert cells['B3']['value']=='1,25' and cells['C3']['value']=='000'



@pytest.mark.parametrize('language',['eng','ara','heb'])
@pytest.mark.parametrize('configuration',['single','combined'])
def test_real_controlled_browser_ocr_language_fixture(tmp_path,monkeypatch,language,configuration):
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))
    manifest=Path(__file__).resolve().parent/'fixtures'/'documents'/'ocr-exact-text.json'
    if not (runtime/'runtime-manifest.json').is_file() or not manifest.is_file():
        pytest.skip('Controlled browser fixtures and local OCR runtime must be installed')
    monkeypatch.setenv('RIWAQ_DOCUMENT_RUNTIME',str(runtime))
    fixture=next(x for x in json.loads(manifest.read_text(encoding='utf-8'))['fixtures'] if x['id']==language)
    source=Path(__file__).resolve().parents[1]/fixture['path']
    import hashlib
    assert hashlib.sha256(source.read_bytes()).hexdigest()==fixture['image_sha256']
    configured=language if configuration=='single' else 'ara+eng+heb'
    page=de.extract_document(source,source.name,tmp_path/'ocr',1,configured)['page']
    raw=page['ocr']['text_raw']
    assert ' '.join(raw.split())==' '.join(fixture['text'].split())
    assert page['ocr']['language']==configured and page['ocr']['runtime_manifest_sha256']
    assert all(e['quality']['review_required'] for e in page['elements'])
    # This proves these clean synthetic fixtures only; it is not a general OCR benchmark.



def test_native_rtl_pdf_never_indexes_unreliable_raw_when_ocr_fails(tmp_path,monkeypatch,pdfium):
    source=Path(__file__).resolve().parent/'fixtures'/'documents'/'arabic-columns-long.pdf'
    if not source.is_file():pytest.skip('Controlled native RTL PDF fixture is required')
    monkeypatch.setattr(de,'run_ocr',lambda *args:{'success':False,'warnings':['simulated local OCR failure']})
    page=de.extract_document(source,source.name,tmp_path/'rtl',12)['page']
    native=[item for item in page['elements'] if item['quality']['method']=='pdfium-native']
    assert native and any(item['text_raw'] for item in native)
    assert all(item['indexable'] is False for item in native)
    assert all(item['quality']['reading_order_verified'] is False for item in native)
    assert page['status']=='failed' and page['native_rtl_fallback'] is True
    assert not any(item.get('indexable',True) and item['text'] for item in page['elements'])


def test_real_native_rtl_fallback_keeps_raw_separate_from_experimental_ocr(tmp_path,monkeypatch,pdfium):
    source=Path(__file__).resolve().parent/'fixtures'/'documents'/'arabic-columns-long.pdf'
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))
    if not source.is_file() or not (runtime/'runtime-manifest.json').is_file():pytest.skip('Controlled PDF and local OCR runtime required')
    monkeypatch.setenv('RIWAQ_DOCUMENT_RUNTIME',str(runtime))
    page=de.extract_document(source,source.name,tmp_path/'rtl',12)['page']
    native=[item for item in page['elements'] if item['quality']['method']=='pdfium-native']
    recognized=[item for item in page['elements'] if item['quality']['method']=='tesseract-tsv']
    assert page['number']==12 and page['status']=='partial'
    assert native and recognized and all(item['indexable'] is False for item in native)
    assert all(item['indexable'] is True and item['quality']['review_required'] for item in recognized)
    assert page['ocr']['runtime_manifest_sha256'] and page['method']=='pdfium-native+tesseract-tsv'
    # This checks honest layer separation, not that the complex page was read correctly.
    # Its observed identifier error is retained in document-rtl-render-experiment.json.



def test_real_python_pptx_default_package_and_worker_cli(tmp_path,monkeypatch):
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))/'python-packages'
    if runtime.is_dir():monkeypatch.syspath_prepend(str(runtime))
    pptx=pytest.importorskip('pptx')
    from pptx.util import Inches
    presentation=pptx.Presentation();slide=presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(2)).text='Riwaq sample slide: 64 units'
    stream=io.BytesIO();presentation.save(stream);data=stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert 'ppt/printerSettings/printerSettings1.bin' in z.namelist()
    inspected=run(tmp_path,data,'slide.pptx',0)
    assert inspected['mime']==de.MIMES['pptx'] and inspected['page_count']==1
    page=run(tmp_path,data,'slide.pptx')['page']
    assert page['elements'][0]['text_raw']=='Riwaq sample slide: 64 units'
    source=tmp_path/'generated.pptx';source.write_bytes(data)
    script=Path(__file__).resolve().parents[1]/'scripts'/'document_worker.py'
    for number in [0,1]:
        output=tmp_path/('worker'+str(number))
        completed=subprocess.run([sys.executable,str(script),'--input',str(source),'--name','slide.pptx',
            '--output',str(output),'--page',str(number),'--languages','ara+eng+heb'],capture_output=True,timeout=10)
        result=json.loads((output/'result.json').read_text(encoding='utf-8'))
        assert completed.returncode==0 and result['success'],result
        if number:assert result['page']['elements'][0]['text_raw']=='Riwaq sample slide: 64 units'


def test_binary_printer_allowance_does_not_admit_unknown_binary_or_wrong_type(tmp_path):
    for name in ['word/embeddings/object1.bin','ppt/printerSettings/printerSettings1.bin']:
        data=archive({'word/document.xml':'<w:document xmlns:w="%s"><w:body/></w:document>'%de.NS['w'],name:b'MZfake'})
        with pytest.raises(de.DocumentError):run(tmp_path,data,'binary.docx',0)



def test_real_openpyxl_boolean_date_percent_format_metadata(tmp_path,monkeypatch):
    from datetime import date
    runtime=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1'))/'python-packages'
    if runtime.is_dir():monkeypatch.syspath_prepend(str(runtime))
    openpyxl=pytest.importorskip('openpyxl')
    book=openpyxl.Workbook();sheet=book.active
    sheet['A1']=True;sheet['A2']=date(2026,11,14);sheet['A3']=7;sheet['A3'].number_format='0%';sheet['A4']=7
    stream=io.BytesIO();book.save(stream);book.close()
    cells={c['address']:c for c in run(tmp_path,stream.getvalue(),'formats.xlsx')['page']['elements'][0]['cells']}
    assert cells['A1']['kind']=='boolean' and cells['A1']['value']=='1'
    assert cells['A2']['kind']=='number' and cells['A2']['raw_value']=='46340'
    assert cells['A2']['number_format_id']>=164 and cells['A2']['number_format']=='yyyy-mm-dd'
    assert cells['A3']['kind']=='number' and cells['A3']['value']=='7'
    assert cells['A3']['number_format_id']==9 and cells['A3']['number_format']=='0%'
    assert cells['A4']['style_index']=='0' and cells['A4']['number_format_id']==0 and cells['A4']['number_format']=='General'


def test_unknown_xlsx_style_is_unresolved_not_general(tmp_path):
    original=zipfile.ZipFile(io.BytesIO(spreadsheet()))
    parts={name:original.read(name) for name in original.namelist() if name!='[Content_Types].xml'}
    parts['xl/worksheets/sheet1.xml']=('<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" s="77"><v>99</v></c></row></sheetData></worksheet>'%de.NS['s']).encode()
    value=run(tmp_path,archive(parts),'unknown-style.xlsx')['page']['elements'][0]['cells'][0]
    assert value['value']=='99' and value['style_index']=='77'
    assert value['number_format_id'] is None and value['number_format'] is None



def test_duplicate_xlsx_cell_addresses_do_not_silently_overwrite(tmp_path):
    original=zipfile.ZipFile(io.BytesIO(spreadsheet()))
    parts={name:original.read(name) for name in original.namelist() if name!='[Content_Types].xml'}
    parts['xl/worksheets/sheet1.xml']=('<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>999</v></c></row></sheetData></worksheet>'%de.NS['s']).encode()
    with pytest.raises(de.DocumentError,match='Duplicate spreadsheet cell address'):
        run(tmp_path,archive(parts),'duplicates.xlsx')
