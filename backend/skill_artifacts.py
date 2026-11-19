"""Bounded, local Office writers for the skill executor.

Only typed data is accepted. These functions do not resolve references, authorize
projects, fetch URLs, read uploaded packages, persist output, or execute Office.
The caller must check session/project and evidence access before and after the
call. Native package verification is not visual or Office acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
import hashlib
from io import BytesIO
import math
import re
import unicodedata
from typing import Literal
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION = '1.1.0'
MAX_OUTPUT = 8 * 1024 * 1024
MAX_INPUT = 150_000
MIME = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}
NS = {
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
    'ct': 'http://schemas.openxmlformats.org/package/2006/content-types',
}


def clean_text(value: str) -> str:
    if any((ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value):
        raise ValueError('Text contains unsupported XML characters')
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*', mode='after')
    @classmethod
    def text_values(cls, value):
        if isinstance(value, str):
            return clean_text(value)
        return value


class Source(Input):
    """Inert display labels. A label is not proof of a verified citation."""
    label: str = Field(min_length=1, max_length=300)
    location: str = Field(default='', max_length=800)


class Table(Input):
    headers: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[str]] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def dimensions(self):
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError('Every table row must match its headers')
        for value in self.headers + [v for row in self.rows for v in row]:
            clean_text(value)
            if len(value) > 1000:
                raise ValueError('Table cell exceeds 1000 characters')
        return self


class Paragraph(Input):
    text: str = Field(min_length=1, max_length=4000)
    style: Literal['normal', 'heading1', 'heading2', 'bullet'] = 'normal'


class DocxInput(Input):
    title: str = Field(min_length=1, max_length=200)
    language: str = Field(default='ar', min_length=2, max_length=35, pattern=r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$')
    direction: Literal['rtl', 'ltr'] = 'rtl'
    paragraphs: list[Paragraph] = Field(default_factory=list, max_length=200)
    tables: list[Table] = Field(default_factory=list, max_length=20)
    sources: list[Source] = Field(default_factory=list, max_length=100)


class Column(Input):
    name: str = Field(min_length=1, max_length=100)
    type: Literal['text', 'number', 'date'] = 'text'


class SpreadsheetInput(Input):
    title: str = Field(min_length=1, max_length=200)
    sheet_name: str = Field(default='Data', min_length=1, max_length=31)
    rtl: bool = True
    columns: list[Column] = Field(min_length=1, max_length=26)
    rows: list[list[str | int | float | None]] = Field(min_length=1, max_length=1000)
    totals: list[int] = Field(default_factory=list, max_length=26)
    sources: list[Source] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def types_and_dimensions(self):
        if re.search(r'[\\/*?:\[\]]', self.sheet_name) or self.sheet_name.startswith("'") or self.sheet_name.endswith("'"):
            raise ValueError('Invalid sheet name')
        if len(set(self.totals)) != len(self.totals):
            raise ValueError('Duplicate total column')
        if any(index < 0 or index >= len(self.columns) or self.columns[index].type != 'number' for index in self.totals):
            raise ValueError('Totals require a valid numeric column index')
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError('Every spreadsheet row must match its columns')
            for column, value in zip(self.columns, row):
                if value is None:
                    continue
                if isinstance(value, bool):
                    raise ValueError('Boolean is not a numeric or text value')
                if column.type == 'number':
                    if not isinstance(value, (int, float)) or abs(value) > 10**14 or not math.isfinite(value):
                        raise ValueError('Numeric columns accept finite numeric values within 1e14')
                    if value != 0 and abs(value) < float.fromhex('0x1.0000000000000p-1022'):
                        raise ValueError('Numeric value is outside the supported Excel normal range; use text to preserve it')
                    if len(Decimal(str(value)).as_tuple().digits) > 15:
                        raise ValueError('Excel supports at most 15 significant numeric digits; use text for identifiers')
                elif column.type == 'date':
                    if not isinstance(value, str):
                        raise ValueError('Dates require ISO YYYY-MM-DD text')
                    parsed = date.fromisoformat(value)
                    if value != parsed.isoformat() or parsed < date(1900, 3, 1):
                        raise ValueError('Dates must be ISO YYYY-MM-DD from 1900-03-01')
                elif not isinstance(value, str) or len(value) > 2000:
                    raise ValueError('Text cells require strings up to 2000 characters')
                if isinstance(value, str):
                    clean_text(value)
        return self


class Slide(Input):
    title: str = Field(min_length=1, max_length=100)
    body: list[str] = Field(default_factory=list, max_length=6)
    table: Table | None = None
    source_labels: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def layout_bounds(self):
        for text in self.body + self.source_labels:
            clean_text(text)
        if any(len(text) > 160 or '\n' in text or '\r' in text for text in self.body):
            raise ValueError('Slide paragraphs allow 160 characters and one paragraph each; split long content into more slides')
        if sum(map(len, self.source_labels)) > 240:
            raise ValueError('Slide source labels exceed the footer; split them across slides')
        if self.table:
            if self.body or len(self.table.headers) > 5 or len(self.table.rows) > 8:
                raise ValueError('Table slides require no body paragraphs and at most 5 columns/8 rows')
            if any(len(text) > 50 or '\n' in text or '\r' in text for text in self.table.headers + [v for row in self.table.rows for v in row]):
                raise ValueError('Slide table cells allow 50 characters on one paragraph')
        _slide_layout(self)
        return self


class SlidesInput(Input):
    title: str = Field(min_length=1, max_length=200)
    language: str = Field(default='ar', min_length=2, max_length=35, pattern=r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$')
    direction: Literal['rtl', 'ltr'] = 'rtl'
    slides: list[Slide] = Field(min_length=1, max_length=30)


SCHEMAS = {'docx': DocxInput, 'xlsx': SpreadsheetInput, 'pptx': SlidesInput}


def _line_count(text: str, width: int, points: int) -> int:
    """Conservative character-width budget, not a font/Office rendering claim.

    Count Latin/Arabic/Hebrew letters as a full em, wide glyphs as two, and
    combining marks as zero. Real fonts generally fit more; uncertain layouts
    are rejected rather than silently clipped or shrunk below the chosen size.
    """
    capacity = max(1, int((width / 12700 - 14.4) / points))
    lines = 0
    for paragraph in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        units = sum(0 if unicodedata.combining(c) or unicodedata.category(c) == 'Cf'
                    else 4 if c == '\t' else 2 if unicodedata.east_asian_width(c) in {'W','F'} else 1
                    for c in paragraph)
        lines += max(1, math.ceil(units / capacity))
    return lines


def _slide_layout(slide):
    width = 10900000
    def height(texts, size):
        return math.ceil((sum(_line_count(text,width,size) for text in texts) * size * 1.2 + 7.2) * 12700)
    if height([slide.title],32) > 1100000:
        raise ValueError('Slide title exceeds the conservative layout space; split or shorten it')
    if slide.body and height(slide.body,20) > 4100000:
        raise ValueError('Slide body exceeds the conservative layout space; split it across slides')
    if slide.source_labels and height(slide.source_labels,11) > 750000:
        raise ValueError('Slide sources exceed the conservative layout space; split them across slides')
    row_heights = []
    if slide.table:
        cell_width = width // len(slide.table.headers)
        for row in [slide.table.headers] + slide.table.rows:
            lines = max(_line_count(text,cell_width,16) for text in row)
            row_heights.append(max(450000,math.ceil((lines*16*1.2+7.2)*12700)))
        if sum(row_heights) > 4100000:
            raise ValueError('Slide table exceeds the conservative layout space; split it across slides')
    return row_heights


@dataclass(frozen=True)
class ArtifactOutput:
    content: bytes
    filename: str
    media_type: str
    sha256: str
    verification: dict
    limitations: tuple[str, ...]


def _element(prefix: str, name: str, attrs=None, text=None):
    node = ET.Element(f'{{{NS[prefix]}}}{name}', attrs or {})
    node.text = text
    return node


def _child(parent, prefix, name, attrs=None, text=None):
    node = _element(prefix, name, attrs, text)
    parent.append(node)
    return node


def _xml(node):
    # Use conventional default namespaces without changing ElementTree's global
    # prefix registry (writers can run concurrently). The independent workbook
    # import engine rejected ns0-prefixed package roots although they are XML-
    # equivalent; default-root OOXML also matches common Office serializers.
    from lxml import etree
    root = etree.fromstring(ET.tostring(node,encoding='utf-8'))
    primary = etree.QName(root).namespace
    result = etree.Element(root.tag,root.attrib,nsmap={None:primary,**{key:value for key,value in root.nsmap.items() if value!=primary}})
    result.text=root.text
    for child in root: result.append(child)
    return etree.tostring(result,encoding='utf-8',xml_declaration=True)


def _zip(parts: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as package:
        for path, data in sorted(parts.items()):
            package.writestr(path, data)
    if output.tell() > MAX_OUTPUT:
        raise ValueError('Office output exceeds 8 MiB')
    return output.getvalue()


def _relationships(entries):
    root = _element('rel', 'Relationships')
    for id_, type_, target in entries:
        _child(root, 'rel', 'Relationship', {'Id': id_, 'Type': NS['r'] + '/' + type_, 'Target': target})
    return _xml(root)


def _content_types(overrides):
    root = _element('ct', 'Types')
    _child(root, 'ct', 'Default', {'Extension': 'rels', 'ContentType': 'application/vnd.openxmlformats-package.relationships+xml'})
    _child(root, 'ct', 'Default', {'Extension': 'xml', 'ContentType': 'application/xml'})
    for path, type_ in overrides:
        _child(root, 'ct', 'Override', {'PartName': '/' + path, 'ContentType': type_})
    return _xml(root)


def _cell(row, address, value, style='0'):
    attrs = {'r': address, 's': style}
    if isinstance(value, str):
        attrs['t'] = 'inlineStr'
        cell = _child(row, 's', 'c', attrs)
        inline = _child(cell, 's', 'is')
        _child(inline, 's', 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}, value)
    else:
        cell = _child(row, 's', 'c', attrs)
        if value is not None:
            _child(cell, 's', 'v', text=str(value))
    return cell


def _sheet(rows, rtl, *, styles=None, totals=None, numeric_totals=None):
    root = _element('s', 'worksheet')
    last = len(rows) + bool(totals)
    _child(root, 's', 'dimension', {'ref': f'A1:{chr(64+len(rows[0]))}{last}'})
    views = _child(root, 's', 'sheetViews')
    view = _child(views, 's', 'sheetView', {'workbookViewId': '0', 'rightToLeft': str(int(rtl))})
    _child(view, 's', 'pane', {'ySplit': '1', 'topLeftCell': 'A2', 'activePane': 'bottomLeft', 'state': 'frozen'})
    cols = _child(root, 's', 'cols')
    for index in range(len(rows[0])):
        width = min(48, max(16, max(len(str(row[index] or '')) for row in rows) + 2))
        _child(cols, 's', 'col', {'min': str(index+1), 'max': str(index+1), 'width': str(width), 'customWidth': '1'})
    sheet_data = _child(root, 's', 'sheetData')
    for row_number, values in enumerate(rows, 1):
        row = _child(sheet_data, 's', 'row', {'r': str(row_number)})
        for index, value in enumerate(values):
            style = '1' if row_number == 1 else (styles[index] if styles else '0')
            _cell(row, chr(65+index) + str(row_number), value, style)
    if totals:
        row = _child(sheet_data, 's', 'row', {'r': str(last)})
        for index in totals:
            cell = _child(row, 's', 'c', {'r': chr(65+index)+str(last), 's': '2'})
            _child(cell, 's', 'f', text=f'SUM({chr(65+index)}2:{chr(65+index)}{len(rows)})')
            _child(cell, 's', 'v', text=numeric_totals[index])
    _child(root, 's', 'autoFilter', {'ref': f'A1:{chr(64+len(rows[0]))}{len(rows)}'})
    return _xml(root)


def calculate_totals(payload: SpreadsheetInput | dict) -> dict[int, str]:
    """Exact decimal SUM for the bounded writer; no eval or arbitrary formulas."""
    model = payload if isinstance(payload, SpreadsheetInput) else SpreadsheetInput.model_validate(payload)
    result = {}
    for index in model.totals:
        values = [Decimal(str(row[index])) for row in model.rows if row[index] is not None]
        nonzero = [value for value in values if value]
        span = max((value.adjusted() for value in nonzero),default=0) - min((value.as_tuple().exponent for value in nonzero),default=0) + 1
        if span > 15:
            raise ValueError('SUM input dynamic range exceeds supported Excel precision; separate scales or preserve values as text')
        with localcontext() as context:
            context.prec = max(32,span+len(str(len(values)))+2)
            total = sum(values, Decimal(0))
        if abs(total) > 10**15 or len(total.as_tuple().digits) > 15:
            raise ValueError('Total exceeds supported Excel numeric precision')
        result[index] = format(total, 'f')
    return result


def _xlsx(model: SpreadsheetInput):
    workbook = _element('s', 'workbook')
    views = _child(workbook, 's', 'bookViews')
    _child(views, 's', 'workbookView')
    sheets = _child(workbook, 's', 'sheets')
    _child(sheets, 's', 'sheet', {'name': model.sheet_name, 'sheetId': '1', '{'+NS['r']+'}id': 'rId1'})
    _child(workbook, 's', 'calcPr', {'calcId': '191029', 'calcMode': 'auto', 'fullCalcOnLoad': '1'})
    rows = [[c.name for c in model.columns]]
    for values in model.rows:
        rows.append([(date.fromisoformat(v)-date(1899, 12, 30)).days if c.type == 'date' and v is not None else v for c, v in zip(model.columns, values)])
    totals = calculate_totals(model)
    parts = {'xl/worksheets/sheet1.xml': _sheet(rows, model.rtl, styles=['3' if c.type == 'date' else '2' if c.type == 'number' else '0' for c in model.columns], totals=model.totals, numeric_totals=totals)}
    overrides = [('xl/workbook.xml', MIME['xlsx'].replace('.sheet', '.sheet.main+xml')), ('xl/styles.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'), ('xl/worksheets/sheet1.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')]
    rels = [('rId1', 'worksheet', 'worksheets/sheet1.xml'), ('rIdStyles', 'styles', 'styles.xml')]
    if model.sources:
        name = 'Sources' if model.sheet_name.casefold() != 'sources' else 'References'
        _child(sheets, 's', 'sheet', {'name': name, 'sheetId': '2', '{'+NS['r']+'}id': 'rId2'})
        parts['xl/worksheets/sheet2.xml'] = _sheet([['Source label', 'Location']] + [[s.label, s.location] for s in model.sources], model.rtl)
        rels.append(('rId2', 'worksheet', 'worksheets/sheet2.xml'))
        overrides.append(('xl/worksheets/sheet2.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'))
    parts['xl/workbook.xml'] = _xml(workbook)
    parts['xl/_rels/workbook.xml.rels'] = _relationships(rels)
    parts['_rels/.rels'] = _relationships([('rId1', 'officeDocument', 'xl/workbook.xml')])
    parts['[Content_Types].xml'] = _content_types(overrides)
    parts['xl/styles.xml'] = f'''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{NS['s']}"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''.encode()
    return _zip(parts), {'rows': len(model.rows), 'columns': len(model.columns), 'formulas': len(totals), 'cached_totals': {str(k): v for k, v in totals.items()}, 'calculation': 'bounded Decimal SUM; Office recalculation requested, not observed'}


def _docx(model: DocxInput):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
    document = Document()
    document.core_properties.title = model.title
    document.core_properties.language = model.language
    document.core_properties.author = ''
    for section in document.sections:
        section.top_margin = section.bottom_margin = Cm(2)
        section.left_margin = section.right_margin = Cm(2)
    for style_name in ('Normal', 'Title', 'Heading 1', 'Heading 2', 'List Bullet'):
        style = document.styles[style_name]
        style.font.name = 'Arial'
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:cs'), 'Arial')
    document.styles['Normal'].font.size = Pt(11)
    document.styles['Normal'].element.get_or_add_rPr().append(OxmlElement('w:szCs'))
    document.styles['Normal'].element.rPr.find(qn('w:szCs')).set(qn('w:val'),'22')
    for style_name in ('Title','Heading 1','Heading 2'):
        document.styles[style_name].paragraph_format.keep_with_next = True

    def direction(paragraph):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if model.direction == 'rtl' else WD_ALIGN_PARAGRAPH.LEFT
        props = paragraph._p.get_or_add_pPr()
        bidi = OxmlElement('w:bidi'); bidi.set(qn('w:val'), '1' if model.direction == 'rtl' else '0'); props.append(bidi)
        for run in paragraph.runs:
            lang = OxmlElement('w:lang'); lang.set(qn('w:val'), model.language); lang.set(qn('w:bidi'), model.language); run._r.get_or_add_rPr().append(lang)
        return paragraph

    direction(document.add_paragraph(_line_endings(model.title), 'Title'))
    styles = {'normal': 'Normal', 'heading1': 'Heading 1', 'heading2': 'Heading 2', 'bullet': 'List Bullet'}
    for paragraph in model.paragraphs:
        direction(document.add_paragraph(_line_endings(paragraph.text), styles[paragraph.style]))
    for data in model.tables:
        table = document.add_table(rows=1, cols=len(data.headers)); table.style = 'Table Grid'
        if model.direction == 'rtl':
            table._tbl.tblPr.append(OxmlElement('w:bidiVisual'))
        repeat = OxmlElement('w:tblHeader'); table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for cell, text in zip(table.rows[0].cells, data.headers):
            cell.text = _line_endings(text); direction(cell.paragraphs[0])
            for run in cell.paragraphs[0].runs: run.bold = True
        for row in data.rows:
            for cell, text in zip(table.add_row().cells, row):
                cell.text = _line_endings(text); direction(cell.paragraphs[0])
    if model.sources:
        direction(document.add_paragraph('المصادر' if model.language.startswith('ar') else 'Sources', 'Heading 1'))
        for source in model.sources:
            direction(document.add_paragraph(_line_endings(source.label + (' — ' + source.location if source.location else ''))))
    output = BytesIO(); document.save(output)
    reopened = Document(BytesIO(output.getvalue()))
    if len(reopened.tables) != len(model.tables):
        raise ValueError('DOCX table verification failed')
    return output.getvalue(), {'paragraphs': len(reopened.paragraphs), 'tables': len(reopened.tables), 'reopened': 'python-docx', 'editable_text': True}


def _p_text(parent, text, model, *, size=2000, bold=False):
    paragraph = _child(parent, 'a', 'p')
    _child(paragraph, 'a', 'pPr', {'algn': 'r' if model.direction == 'rtl' else 'l', 'rtl': str(int(model.direction == 'rtl'))})
    run = _child(paragraph, 'a', 'r')
    props = _child(run, 'a', 'rPr', {'lang': model.language, 'sz': str(size), 'b': str(int(bold))})
    _child(props, 'a', 'solidFill').append(_element('a', 'srgbClr', {'val': '202020'}))
    for script in ('latin', 'ea', 'cs'): _child(props, 'a', script, {'typeface': 'Arial'})
    _child(run, 'a', 't', text=text)
    _child(paragraph, 'a', 'endParaRPr', {'lang': model.language, 'sz': str(size)})


def _sp_tree():
    tree = _element('p', 'spTree')
    props = _child(tree, 'p', 'nvGrpSpPr')
    _child(props, 'p', 'cNvPr', {'id': '1', 'name': ''}); _child(props, 'p', 'cNvGrpSpPr'); _child(props, 'p', 'nvPr')
    xfrm = _child(_child(tree, 'p', 'grpSpPr'), 'a', 'xfrm')
    for tag, attrs in [('off', {'x':'0','y':'0'}),('ext', {'cx':'0','cy':'0'}),('chOff', {'x':'0','y':'0'}),('chExt', {'cx':'0','cy':'0'})]: _child(xfrm,'a',tag,attrs)
    return tree


def _textbox(tree, id_, name, rect, paragraphs, model, size=2000, bold=False):
    shape = _child(tree, 'p', 'sp')
    nv = _child(shape, 'p', 'nvSpPr'); _child(nv, 'p', 'cNvPr', {'id': str(id_), 'name': name}); _child(nv, 'p', 'cNvSpPr', {'txBox': '1'}); _child(nv, 'p', 'nvPr')
    props = _child(shape, 'p', 'spPr'); transform = _child(props, 'a', 'xfrm')
    _child(transform, 'a', 'off', {'x': str(rect[0]), 'y': str(rect[1])}); _child(transform, 'a', 'ext', {'cx': str(rect[2]), 'cy': str(rect[3])})
    _child(_child(props, 'a', 'prstGeom', {'prst': 'rect'}), 'a', 'avLst'); _child(props, 'a', 'noFill')
    body = _child(shape, 'p', 'txBody'); _child(_child(body, 'a', 'bodyPr', {'wrap': 'square', 'rtlCol': str(int(model.direction == 'rtl')),'lIns':'91440','rIns':'91440','tIns':'45720','bIns':'45720'}),'a','noAutofit'); _child(body, 'a', 'lstStyle')
    for text in paragraphs: _p_text(body, text, model, size=size, bold=bold)


def _slide_table(tree, table, model, row_heights):
    frame = _child(tree, 'p', 'graphicFrame')
    nv = _child(frame, 'p', 'nvGraphicFramePr'); _child(nv,'p','cNvPr',{'id':'4','name':'Editable table'}); _child(nv,'p','cNvGraphicFramePr'); _child(nv,'p','nvPr')
    transform = _child(frame, 'p', 'xfrm'); _child(transform,'a','off',{'x':'600000','y':'1600000'}); _child(transform,'a','ext',{'cx':'10900000','cy':str(sum(row_heights))})
    graphic = _child(frame, 'a', 'graphic'); data = _child(graphic,'a','graphicData',{'uri':'http://schemas.openxmlformats.org/drawingml/2006/table'}); table_xml = _child(data,'a','tbl')
    _child(table_xml,'a','tblPr',{'firstRow':'1','bandRow':'1','rtl':str(int(model.direction=='rtl'))})
    grid = _child(table_xml,'a','tblGrid')
    for _ in table.headers: _child(grid,'a','gridCol',{'w':str(10900000//len(table.headers))})
    for index,row in enumerate([table.headers]+table.rows):
        tr = _child(table_xml,'a','tr',{'h':str(row_heights[index])})
        for value in row:
            cell = _child(tr,'a','tc'); body = _child(cell,'a','txBody'); _child(body,'a','bodyPr'); _child(body,'a','lstStyle'); _p_text(body,value,model,size=1600,bold=index==0); _child(cell,'a','tcPr')


def _pptx(model: SlidesInput):
    from docx import Document
    template = BytesIO(); Document().save(template)
    with ZipFile(template) as package: theme = package.read('word/theme/theme1.xml')
    presentation = _element('p','presentation')
    masters = _child(presentation,'p','sldMasterIdLst'); _child(masters,'p','sldMasterId',{'id':'2147483648','{'+NS['r']+'}id':'rIdMaster'})
    ids = _child(presentation,'p','sldIdLst')
    for index in range(1,len(model.slides)+1): _child(ids,'p','sldId',{'id':str(255+index),'{'+NS['r']+'}id':'rId'+str(index)})
    _child(presentation,'p','sldSz',{'cx':'12192000','cy':'6858000','type':'screen16x9'}); _child(presentation,'p','notesSz',{'cx':'6858000','cy':'9144000'})
    master = _element('p','sldMaster'); _child(master,'p','cSld').append(_sp_tree())
    _child(master,'p','clrMap',dict(zip(('bg1','tx1','bg2','tx2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink'),('lt1','dk1','lt2','dk2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink'))))
    layouts = _child(master,'p','sldLayoutIdLst'); _child(layouts,'p','sldLayoutId',{'id':'2147483649','{'+NS['r']+'}id':'rIdLayout'})
    layout = _element('p','sldLayout',{'type':'blank','preserve':'1'}); _child(layout,'p','cSld',{'name':'Blank'}).append(_sp_tree()); _child(_child(layout,'p','clrMapOvr'),'a','masterClrMapping')
    parts = {'ppt/presentation.xml':_xml(presentation),'ppt/slideMasters/slideMaster1.xml':_xml(master),'ppt/slideLayouts/slideLayout1.xml':_xml(layout),'ppt/theme/theme1.xml':theme}
    parts['_rels/.rels'] = _relationships([('rId1','officeDocument','ppt/presentation.xml')])
    parts['ppt/_rels/presentation.xml.rels'] = _relationships([('rIdMaster','slideMaster','slideMasters/slideMaster1.xml')]+[('rId'+str(i),'slide',f'slides/slide{i}.xml') for i in range(1,len(model.slides)+1)])
    parts['ppt/slideMasters/_rels/slideMaster1.xml.rels'] = _relationships([('rIdLayout','slideLayout','../slideLayouts/slideLayout1.xml'),('rIdTheme','theme','../theme/theme1.xml')])
    parts['ppt/slideLayouts/_rels/slideLayout1.xml.rels'] = _relationships([('rIdMaster','slideMaster','../slideMasters/slideMaster1.xml')])
    overrides = [('ppt/presentation.xml','application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'),('ppt/slideMasters/slideMaster1.xml','application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml'),('ppt/slideLayouts/slideLayout1.xml','application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml'),('ppt/theme/theme1.xml','application/vnd.openxmlformats-officedocument.theme+xml')]
    for index,slide in enumerate(model.slides,1):
        root = _element('p','sld'); content = _child(root,'p','cSld'); tree = _sp_tree(); content.append(tree)
        row_heights = _slide_layout(slide)
        _textbox(tree,2,'Title',(600000,350000,10900000,1100000),[slide.title],model,size=3200,bold=True)
        if slide.table: _slide_table(tree,slide.table,model,row_heights)
        else: _textbox(tree,3,'Body',(600000,1600000,10900000,4100000),slide.body or [''],model)
        if slide.source_labels: _textbox(tree,5,'Source labels',(600000,5950000,10900000,750000),slide.source_labels,model,size=1100)
        _child(_child(root,'p','clrMapOvr'),'a','masterClrMapping')
        path = f'ppt/slides/slide{index}.xml'; parts[path] = _xml(root)
        parts[f'ppt/slides/_rels/slide{index}.xml.rels'] = _relationships([('rIdLayout','slideLayout','../slideLayouts/slideLayout1.xml')])
        overrides.append((path,'application/vnd.openxmlformats-officedocument.presentationml.slide+xml'))
    parts['[Content_Types].xml'] = _content_types(overrides)
    return _zip(parts), {'slides':len(model.slides),'editable_text':True,'native_tables':sum(s.table is not None for s in model.slides),'canvas_emu':[12192000,6858000],'layout_check':'conservative em-width budget; not rendered Office layout'}


def _line_endings(text):
    return text.replace('\r\n','\n').replace('\r','\n')


def _verify_content(kind, content, model):
    """Reopen the finished bytes and compare every authored content item.

    A writer receipt or valid ZIP alone cannot prove that requested content was
    retained. This check runs after the writer and fails before persistence.
    """
    def require(condition):
        if not condition: raise ValueError(f'{kind.upper()} semantic content verification failed')
    with ZipFile(BytesIO(content)) as package:
        if kind == 'docx':
            from docx import Document
            document = Document(BytesIO(content))
            expected = [model.title] + [p.text for p in model.paragraphs]
            if model.sources:
                expected += ['المصادر' if model.language.startswith('ar') else 'Sources']
                expected += [s.label + (' — ' + s.location if s.location else '') for s in model.sources]
            require([p.text for p in document.paragraphs] == [_line_endings(t) for t in expected])
            require(len(document.tables) == len(model.tables))
            for actual,table in zip(document.tables,model.tables):
                require([[cell.text for cell in row.cells] for row in actual.rows] == [[_line_endings(t) for t in row] for row in [table.headers]+table.rows])
        elif kind == 'xlsx':
            sheet = ET.fromstring(package.read('xl/worksheets/sheet1.xml'))
            cells = {cell.attrib['r']:cell for cell in sheet.findall('.//s:c',NS)}
            rows = [[column.name for column in model.columns]] + model.rows
            expected_addresses = set()
            for number,row in enumerate(rows,1):
                for index,value in enumerate(row):
                    address=chr(65+index)+str(number);expected_addresses.add(address)
                    require(address in cells);cell=cells[address]
                    require(cell.find('s:f',NS) is None)
                    if number>1 and model.columns[index].type=='date' and value is not None:
                        value=(date.fromisoformat(value)-date(1899,12,30)).days
                    if isinstance(value,str):
                        require(cell.attrib.get('t')=='inlineStr' and cell.findtext('s:is/s:t',namespaces=NS)==value)
                    else:
                        actual=cell.findtext('s:v',namespaces=NS)
                        require(actual is None if value is None else actual is not None and Decimal(actual)==Decimal(str(value)))
            totals=calculate_totals(model)
            for index,total in totals.items():
                address=chr(65+index)+str(len(rows)+1);expected_addresses.add(address)
                require(address in cells);cell=cells[address]
                require(cell.findtext('s:f',namespaces=NS)==f'SUM({chr(65+index)}2:{chr(65+index)}{len(rows)})')
                require(cell.findtext('s:v',namespaces=NS)==total)
            require(set(cells)==expected_addresses)
            if model.sources:
                source=ET.fromstring(package.read('xl/worksheets/sheet2.xml'))
                require([cell.findtext('s:is/s:t',default='',namespaces=NS) for cell in source.findall('.//s:c',NS)]==['Source label','Location']+[text for s in model.sources for text in (s.label,s.location)])
        else:
            for index,slide in enumerate(model.slides,1):
                root=ET.fromstring(package.read(f'ppt/slides/slide{index}.xml'))
                expected=[slide.title]
                expected += [text for row in [slide.table.headers]+slide.table.rows for text in row] if slide.table else slide.body or ['']
                expected += slide.source_labels
                require([(node.text or '') for node in root.findall('.//a:t',NS)]==expected)
                require(root.find('.//p:pic',NS) is None)
    return {'content_integrity':True,'office_rendered':False}


def build_artifact(kind: Literal['docx','xlsx','pptx'], payload: dict) -> ArtifactOutput:
    """Generate bytes only; caller owns authorization, output ACL and persistence."""
    if kind not in SCHEMAS:
        raise ValueError('Unsupported Office format')
    model = SCHEMAS[kind].model_validate(payload)
    if len(model.model_dump_json()) > MAX_INPUT:
        raise ValueError('Office input exceeds 150000 characters')
    content, verification = {'docx':_docx,'xlsx':_xlsx,'pptx':_pptx}[kind](model)
    if len(content) > MAX_OUTPUT:
        raise ValueError('Office output exceeds 8 MiB')
    with ZipFile(BytesIO(content)) as package:
        if package.testzip() is not None:
            raise ValueError('Office ZIP verification failed')
        for path in package.namelist():
            if path.endswith(('.xml','.rels')):
                ET.fromstring(package.read(path))
        if '[Content_Types].xml' not in package.namelist():
            raise ValueError('Office package content types missing')
    semantic = _verify_content(kind,content,model)
    return ArtifactOutput(content, f'riwaq-document.{kind}', MIME[kind], hashlib.sha256(content).hexdigest(),
                          {'writer_version':VERSION,'package_integrity':True,**verification,**semantic},
                          ('Visual rendering, pagination and font coverage require review in the target Office application.',
                           'Source labels are supplied data; the caller must resolve and authorize evidence separately.',
                           'XLSX supports native SUM totals only; no arbitrary formulas, charts, macros or external connections.' if kind=='xlsx' else
                           'Native tables and text are editable; custom templates, charts and embedded media are not supported.'))
