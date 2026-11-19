"""Independent semantic export and conservative layout acceptance, synthetic data."""
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from backend import skill_artifacts as artifacts


def numbers(values):
    return {'title':'Typed values','columns':[{'name':'Amount','type':'number'}],
            'rows':[[value] for value in values],'totals':[0]}


def rewrite(content, path, change):
    out=BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(out,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            raw=source.read(name)
            if name==path:
                root=ET.fromstring(raw);change(root);raw=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            target.writestr(name,raw)
    return out.getvalue()


def test_excel_subnormal_is_rejected_instead_of_becoming_zero_on_open():
    with pytest.raises(ValueError,match='range'):
        artifacts.build_artifact('xlsx',numbers([5e-324]))


def test_total_cancellation_does_not_silently_lose_small_input():
    # Baseline Decimal28 SUM silently writes zero for a nonzero exact sum.
    with pytest.raises(ValueError,match='precision|range'):
        artifacts.build_artifact('xlsx',numbers([10**14,1e-20,-10**14]))


@pytest.mark.parametrize('slide',[
    {'title':'W'*100},
    {'title':'Dense body','body':['W'*160]*6},
    {'title':'Dense table','table':{'headers':['W'*50]*5,'rows':[['W'*50]*5 for _ in range(8)]}},
])
def test_presentation_rejects_content_that_exceeds_fixed_slide_space(slide):
    with pytest.raises(ValueError,match='layout|space|split'):
        artifacts.build_artifact('pptx',{'title':'Synthetic layout','slides':[slide]})


@pytest.mark.parametrize('kind,payload,part,element',[
    ('xlsx',numbers([0,None,2.75]),'xl/worksheets/sheet1.xml',f".//{{{artifacts.NS['s']}}}sheetData/{{{artifacts.NS['s']}}}row/{{{artifacts.NS['s']}}}c"),
    ('docx',{'title':'Complete report','paragraphs':[{'text':'Heading','style':'heading1'},{'text':'LAST REQUIRED SECTION'}]},'word/document.xml','{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body/{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'),
    ('pptx',{'title':'Complete deck','slides':[{'title':'Required title','body':['LAST REQUIRED SENTENCE']}]},'ppt/slides/slide1.xml',f".//{{{artifacts.NS['a']}}}t"),
])
def test_success_receipt_rejects_lost_content_after_writer(monkeypatch,kind,payload,part,element):
    original=getattr(artifacts,'_'+kind)
    def broken(model):
        content,metadata=original(model)
        def remove_last(root):
            victim=root.findall(element)[-1]
            parent=next(node for node in root.iter() if victim in list(node));parent.remove(victim)
        return rewrite(content,part,remove_last),metadata
    monkeypatch.setattr(artifacts,'_'+kind,broken)
    with pytest.raises(ValueError,match='content|semantic|verification'):
        artifacts.build_artifact(kind,payload)


def test_docx_line_endings_are_normalized_once_without_extra_paragraphs():
    from docx import Document
    output=artifacts.build_artifact('docx',{'title':'Report','paragraphs':[{'text':'A\r\nB\rC\nD\t0'}]})
    document=Document(BytesIO(output.content))
    assert document.paragraphs[1].text=='A\nB\nC\nD\t0'


def test_mixed_language_editable_content_verification_is_explicit():
    for kind,payload in [
        ('xlsx',numbers([0,None,1.25,-0.25])),
        ('docx',{'title':'رواق','paragraphs':[{'text':'לא לשנות 50 ₪ — do not change API'}]}),
        ('pptx',{'title':'رواق','slides':[{'title':'اختبار — בדיקה','body':['0 ≠ blank; لا تغيّر 50 ₪']}]}),
    ]:
        output=artifacts.build_artifact(kind,payload)
        assert output.verification.get('content_integrity') is True
        assert output.verification.get('office_rendered') is False


def test_presentation_table_uses_standard_native_graphic_data_uri():
    output=artifacts.build_artifact('pptx',{'title':'Native table','slides':[{'title':'Values','table':{'headers':['ID','Value'],'rows':[['001','0']]}}]})
    with ZipFile(BytesIO(output.content)) as package:
        slide=ET.fromstring(package.read('ppt/slides/slide1.xml'))
    assert slide.find('.//a:graphicData',artifacts.NS).get('uri')=='http://schemas.openxmlformats.org/drawingml/2006/table'


def test_table_row_height_accounts_for_content_and_stays_before_footer():
    output=artifacts.build_artifact('pptx',{'title':'Native table','slides':[{'title':'Values','table':{'headers':['ID','A','B','C','D'],'rows':[['001','longer value wraps','0','','12.5']]},'source_labels':['synthetic p2']}]})
    with ZipFile(BytesIO(output.content)) as package:
        slide=ET.fromstring(package.read('ppt/slides/slide1.xml'))
    rows=slide.findall('.//a:tr',artifacts.NS)
    assert int(rows[1].get('h'))>int(rows[0].get('h'))
    frame=slide.find('.//p:graphicFrame/p:xfrm',artifacts.NS)
    assert int(frame.find('a:off',artifacts.NS).get('y'))+int(frame.find('a:ext',artifacts.NS).get('cy'))<5950000


def test_identifiers_explicitly_use_text_format_and_dates_iso_display():
    output=artifacts.build_artifact('xlsx',{'title':'Types','columns':[{'name':'ID','type':'text'},{'name':'Date','type':'date'}],'rows':[['001','2026-11-16']]})
    with ZipFile(BytesIO(output.content)) as package:
        styles=ET.fromstring(package.read('xl/styles.xml'))
    formats=styles.find('s:cellXfs',artifacts.NS)
    assert formats[0].get('numFmtId')=='49'
    date_format=next(f for f in styles.findall('s:numFmts/s:numFmt',artifacts.NS) if f.get('numFmtId')==formats[3].get('numFmtId'))
    assert date_format.get('formatCode')=='yyyy-mm-dd'
