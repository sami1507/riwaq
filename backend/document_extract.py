"""Bounded local document extraction. No DB, network, model or document execution.

PDFium operations must run in the caller's isolated single-page process. Coordinates
refer to the generated preview (top-left origin). Office locators describe actual
XML structure, not inferred printed pages. OCR scores are engine signals, not accuracy.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
import warnings
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

VERSION = 'document-extract-v1'
PDF_RENDER_SCALE = 2.0
ROOT = Path(__file__).resolve().parent.parent
MIMES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp',
 'txt': 'text/plain', 'md': 'text/markdown', 'csv': 'text/csv',
 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
 'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
 's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
 'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
 'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
 'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 8 * 1024 * 1024
    max_pages: int = 200
    max_pixels: int = 8_000_000
    max_extracted_bytes: int = 2 * 1024 * 1024
    max_zip_bytes: int = 32 * 1024 * 1024
    max_zip_entries: int = 2000
    max_zip_ratio: int = 250
    max_elements: int = 20_000
    max_cells: int = 20_000
    ocr_timeout_seconds: int = 60


def load_limits(path=None):
    defaults = asdict(Limits())
    path = path or os.environ.get('RIWAQ_DOCUMENT_LIMITS')
    if not path:
        return Limits()
    raw = Path(path).read_bytes()
    if len(raw) > 8192:
        raise DocumentError('Limits configuration exceeds 8192 bytes')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise DocumentError('Unknown document limits')
    for key, number in value.items():
        if type(number) is not int or not 1 <= number <= defaults[key]:
            raise DocumentError('Limits may only reduce the validated defaults: ' + key)
    return Limits(**(defaults | value))


def languages(value):
    parts = value.split('+')
    if not parts or len(parts) > 3 or len(set(parts)) != len(parts) or any(x not in {'ara','eng','heb'} for x in parts):
        raise DocumentError('OCR languages must be a unique combination of ara, eng and heb')
    return '+'.join(parts)


def xml_text(data):
    try:
        text=data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise DocumentError('Office XML must use UTF-8') from exc
    if '\x00' in text or re.search(r'<!\s*(?:DOCTYPE|ENTITY)',text,re.I):
        raise DocumentError('XML entities, DTDs and UTF-16 XML are not accepted')
    return text


def xml(data):
    text=xml_text(data)
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise DocumentError('Malformed Office XML') from exc


class Archive:
    def __init__(self, data, limits):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            entries = self.zip.infolist()
        except zipfile.BadZipFile as exc:
            raise DocumentError('Invalid ZIP container') from exc
        names = [entry.filename for entry in entries]
        if len(entries) > limits.max_zip_entries or len(set(names)) != len(names):
            raise DocumentError('ZIP entry limit or duplicate names')
        if sum(entry.file_size for entry in entries) > limits.max_zip_bytes:
            raise DocumentError('Expanded ZIP exceeds limit')
        printer_parts=[]
        for entry in entries:
            name = entry.orig_filename
            if '\\' in name or name.startswith('/') or ':' in name or '..' in PurePosixPath(name).parts or '\x00' in name:
                raise DocumentError('Unsafe ZIP member path')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise DocumentError('ZIP symbolic links are not accepted')
            if entry.flag_bits & 1 or entry.file_size > max(1,entry.compress_size) * limits.max_zip_ratio:
                raise DocumentError('Encrypted or excessive-ratio ZIP entry')
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise DocumentError('Unsupported ZIP compression')
            if 'vbaproject' in name.lower() or name.lower().endswith(('.exe','.dll','.com')):
                raise DocumentError('Macros and embedded executable objects are not accepted')
            if name.lower().endswith('.bin'):
                if not re.fullmatch(r'(ppt|xl|word)/printerSettings/printerSettings[1-9][0-9]*\.bin',name):
                    raise DocumentError('Embedded binary objects are not accepted')
                printer_parts.append(name)
        self.names = set(names)
        self.limit = limits.max_zip_bytes
        manifest = self.read('[Content_Types].xml')
        manifest_tree=xml(manifest)
        defaults={part.get('Extension'):part.get('ContentType') for part in manifest_tree if part.tag.rsplit('}',1)[-1]=='Default'}
        overrides={part.get('PartName','').lstrip('/'):part.get('ContentType') for part in manifest_tree if part.tag.rsplit('}',1)[-1]=='Override'}
        printer_types={'ppt':'presentationml','xl':'spreadsheetml','word':'wordprocessingml'}
        for name in printer_parts:
            declared=overrides.get(name,defaults.get('bin'))
            expected='application/vnd.openxmlformats-officedocument.'+printer_types[name.split('/')[0]]+'.printerSettings'
            if declared!=expected:raise DocumentError('Binary printer settings content type is invalid')
        # Standard printer settings remain opaque: never parsed, applied or executed.
        if b'macroenabled' in manifest.lower() or b'vba' in manifest.lower():
            raise DocumentError('Macro-enabled Office containers are not accepted')
        # Inspect every XML member for declarations; never resolve external entities.
        for name in names:
            if name.endswith(('.xml','.rels')):
                data = self.read(name)
                xml_text(data)

    def read(self, name):
        try:
            with self.zip.open(name) as stream:
                data = stream.read(self.limit + 1)
            if len(data) > self.limit:
                raise DocumentError('Expanded ZIP entry exceeds limit')
            return data
        except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            raise DocumentError('Missing or damaged Office part: ' + name) from exc

    def tree(self, name):
        return xml(self.read(name))

    def relations(self, part):
        path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels')
        if path not in self.names:
            return {}
        result = {}
        for item in self.tree(path):
            if item.get('TargetMode') == 'External':
                continue
            target = item.get('Target','')
            if not target or ':' in target or '\\' in target or target.startswith('//'):
                raise DocumentError('Invalid internal relationship')
            # OPC absolute targets are package-root paths, never filesystem paths.
            resolved = posixpath.normpath(target[1:] if target.startswith('/') else posixpath.join(posixpath.dirname(part),target))
            if resolved.startswith('../') or resolved not in self.names:
                raise DocumentError('Missing or escaping internal relationship')
            result[item.get('Id')] = resolved
        return result


def sniff(data, filename, limits):
    if not data or len(data) > limits.max_input_bytes:
        raise DocumentError('Document must contain 1 to %d bytes' % limits.max_input_bytes)
    if len(filename) > 255 or not filename or re.search(r'[\\/\x00-\x1f]',filename):
        raise DocumentError('Original filename must be a basename without controls')
    if data.startswith(b'%PDF-'):
        return 'pdf', None
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png', None
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg', None
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp', None
    if data.startswith(b'PK'):
        archive = Archive(data,limits)
        candidates = [kind for kind,path in [('docx','word/document.xml'),('xlsx','xl/workbook.xml'),('pptx','ppt/presentation.xml')] if path in archive.names]
        if len(candidates) != 1:
            raise DocumentError('Unsupported or ambiguous Office container')
        return candidates[0], archive
    extension = Path(filename).suffix.lower().lstrip('.')
    if extension not in {'txt','md','csv'}:
        raise DocumentError('Unsupported actual type or filename/type mismatch')
    try:
        data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise DocumentError('Text files must be UTF-8') from exc
    if b'\x00' in data:
        raise DocumentError('Binary data is not a UTF-8 text document')
    return extension, None


def element(number, index, kind, raw='', bbox=None, locator=None, quality=None, **extras):
    return {'id': 'p%d-e%05d' % (number,index), 'type':kind, 'text_raw':raw, 'text':raw,
            'bbox':bbox, 'locator':{'page':number, **(locator or {})},
            'quality':quality or {'method':'native','confidence':None}, **extras}


def page_result(number, kind, method, elements, width=None, height=None, language=None, warnings=None, preview=None, status='complete', **extras):
    return {'number':number,'width':width,'height':height,'kind':kind,'status':status,
            'method':method,'language':language,'warnings':warnings or [],'elements':elements,
            'preview_file':preview, 'printed_number':None, **extras}


def bound_result(result, limits):
    page = result.get('page')
    if page and len(page['elements']) > limits.max_elements:
        raise DocumentError('Page element limit exceeded')
    # Bound the complete structured extraction, not only its concatenated text.
    if len(json.dumps(result,ensure_ascii=False).encode('utf-8')) > limits.max_extracted_bytes:
        raise DocumentError('Structured extraction exceeds byte limit')
    return result


def column_name(number):
    value = ''
    while number:
        number, digit = divmod(number-1,26)
        value = chr(65+digit)+value
    return value


def cell(address,row,column,value,kind='text',formula=None,cached_value=None,**extras):
    return {'address':address,'row':row,'column':column,'value':value,'kind':kind,
            'formula':formula,'cached_value':cached_value,**extras}


def plain_page(kind, data, limits):
    raw = data.decode('utf-8-sig')
    if len(raw.encode('utf-8')) > limits.max_extracted_bytes:
        raise DocumentError('Text exceeds extracted byte limit')
    if kind != 'csv':
        return page_result(1,'text','utf8',[element(1,1,'text',raw,locator={'lines':[1,max(1,len(raw.splitlines()))]})])
    csv.field_size_limit(limits.max_extracted_bytes)
    rows, cells, source_lines = [], [], []
    csv_warnings=['CSV values remain strings; leading zeros, empty fields and blank rows are preserved.']
    sample=raw.encode('utf-8')[:65536].decode('utf-8',errors='ignore')
    try:delimiter=csv.Sniffer().sniff(sample,delimiters=',;\t').delimiter
    except csv.Error:
        delimiter=','
        csv_warnings.append('Delimiter was ambiguous in the first 64 KiB; comma was used. Inspect raw text before using cells.')
    reader = csv.reader(io.StringIO(raw,newline=''),delimiter=delimiter,strict=True)
    try:
        previous = 0
        for r, values in enumerate(reader,1):
            source_lines.append([previous+1,reader.line_num]);previous=reader.line_num
            rows.append(values)
            for c,value in enumerate(values,1):
                cells.append(cell(column_name(c)+str(r),r,c,value,'blank' if value=='' else 'text'))
                if len(cells)>limits.max_cells:
                    raise DocumentError('CSV cell limit exceeded')
    except csv.Error as exc:
        raise DocumentError('Malformed CSV') from exc
    return page_result(1,'sheet','csv-stdlib',[element(1,1,'table',raw,locator={'lines':[1,max(1,reader.line_num)]},
        cells=cells, row_count=len(rows), row_lengths=[len(r) for r in rows], source_lines=source_lines,delimiter=delimiter)],
        warnings=csv_warnings)


def text_runs(node, namespace):
    values=[]
    for part in node.iter():
        local=part.tag.rsplit('}',1)[-1]
        if part.tag=='{'+namespace+'}t':values.append(part.text or '')
        elif part.tag=='{'+namespace+'}tab':values.append('\t')
        elif part.tag in {'{'+namespace+'}br','{'+namespace+'}cr'}:values.append('\n')
    return ''.join(values)


def docx_page(archive, limits):
    body=archive.tree('word/document.xml').find('w:body',NS)
    if body is None:raise DocumentError('DOCX has no document body')
    elements=[];paragraph=0;table=0;cells_count=0
    for part in body:
        if part.tag=='{'+NS['w']+'}p':
            paragraph+=1
            raw=text_runs(part,NS['w'])
            elements.append(element(1,len(elements)+1,'text',raw,locator={'paragraph':paragraph}))
            for drawing in part.findall('.//w:drawing',NS):
                elements.append(element(1,len(elements)+1,'image','',locator={'paragraph':paragraph},
                    quality={'method':'office-xml','confidence':None,'readable':False},
                    warnings=['Embedded image content was not OCRed.']))
        elif part.tag=='{'+NS['w']+'}tbl':
            table+=1;cells=[];lines=[]
            for r,row in enumerate(part.findall('w:tr',NS),1):
                line=[];column=1
                for entry in row.findall('w:tc',NS):
                    value='\n'.join(text_runs(p,NS['w']) for p in entry.findall('w:p',NS))
                    span=entry.find('w:tcPr/w:gridSpan',NS)
                    colspan=int(span.get('{'+NS['w']+'}val','1')) if span is not None else 1
                    if not 1<=colspan<=1000:raise DocumentError('DOCX grid span exceeds bound')
                    merged=entry.find('w:tcPr/w:vMerge',NS)
                    cells.append(cell('R%dC%d'%(r,column),r,column,value,colspan=colspan,
                        vertical_merge=None if merged is None else merged.get('{'+NS['w']+'}val','continue')))
                    line.append(value);column+=colspan;cells_count+=1
                    if cells_count>limits.max_cells:raise DocumentError('DOCX cell limit exceeded')
                lines.append('\t'.join(line))
            elements.append(element(1,len(elements)+1,'table','\n'.join(lines),locator={'table':table},cells=cells))
        if len(elements)>limits.max_elements:raise DocumentError('DOCX element limit exceeded')
    return page_result(1,'text','ooxml-body',elements,warnings=[
        'Logical document body, not Word pagination; headers, footers, comments and embedded-image text are not extracted.',
        'External relationships, macros and fields are not executed.'])


def office_pages(kind, archive):
    if kind=='docx':return [('body','word/document.xml')]
    part='xl/workbook.xml' if kind=='xlsx' else 'ppt/presentation.xml'
    tree=archive.tree(part);relations=archive.relations(part)
    result=[]
    nodes=tree.findall('s:sheets/s:sheet',NS) if kind=='xlsx' else tree.findall('p:sldIdLst/p:sldId',NS)
    for index,node in enumerate(nodes,1):
        rid=node.get('{'+NS['r']+'}id')
        if rid not in relations:raise DocumentError('Missing sheet/slide relationship')
        result.append((node.get('name',str(index)),relations[rid]))
    return result


def xlsx_formats(archive):
    # This reads format metadata only; it never formats dates, converts units,
    # evaluates formulas or loads an external style resource.
    builtins={0:'General',1:'0',2:'0.00',9:'0%',10:'0.00%',14:'mm-dd-yy',
              15:'d-mmm-yy',16:'d-mmm',17:'mmm-yy',18:'h:mm AM/PM',
              19:'h:mm:ss AM/PM',20:'h:mm',21:'h:mm:ss',22:'m/d/yy h:mm',49:'@'}
    if 'xl/styles.xml' not in archive.names:return [(0,'General')]
    styles=archive.tree('xl/styles.xml');custom={};duplicates=set()
    for item in styles.findall('s:numFmts/s:numFmt',NS):
        raw=item.get('numFmtId','')
        if not re.fullmatch(r'[0-9]{1,6}',raw):continue
        identifier=int(raw)
        if identifier in custom:duplicates.add(identifier)
        custom[identifier]=item.get('formatCode')
    bases=styles.findall('s:cellStyleXfs/s:xf',NS)
    result=[]
    def fmt_id(value):
        return int(value) if isinstance(value,str) and re.fullmatch(r'[0-9]{1,6}',value) else None
    for xf in styles.findall('s:cellXfs/s:xf',NS):
        base_index=fmt_id(xf.get('xfId','0'))
        inherited=fmt_id(bases[base_index].get('numFmtId','0')) if base_index is not None and base_index<len(bases) else None
        own=fmt_id(xf.get('numFmtId'))
        effective=inherited if xf.get('applyNumberFormat')=='0' or own is None else own
        if effective in duplicates:effective=None
        code=None if effective is None else custom.get(effective,builtins.get(effective))
        result.append((effective,code))
    return result


def xlsx_page(archive, title, part, number, limits):
    formats=xlsx_formats(archive)
    shared=[]
    if 'xl/sharedStrings.xml' in archive.names:
        for entry in archive.tree('xl/sharedStrings.xml').findall('s:si',NS):
            shared.append(''.join(x.text or '' for x in entry.iter('{'+NS['s']+'}t')))
    tree=archive.tree(part);cells=[];seen_addresses=set()
    for node in tree.findall('s:sheetData/s:row/s:c',NS):
        address=node.get('r','')
        match=re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})',address)
        if not match:raise DocumentError('Invalid spreadsheet cell address')
        if address in seen_addresses:raise DocumentError('Duplicate spreadsheet cell address: '+address)
        seen_addresses.add(address)
        column=0
        for char in match[1]:column=column*26+ord(char)-64
        row=int(match[2])
        if column>16384 or row>1048576:raise DocumentError('Cell outside XLSX grid')
        typ=node.get('t','n');value_node=node.find('s:v',NS);raw=value_node.text if value_node is not None and value_node.text is not None else ''
        formula_node=node.find('s:f',NS);formula=None if formula_node is None else (formula_node.text or '')
        if typ=='s':
            try:
                if not re.fullmatch(r'[0-9]+',raw):raise ValueError('Nonnegative shared index required')
                value=shared[int(raw)]
            except (ValueError,IndexError) as exc:raise DocumentError('Invalid shared string reference') from exc
        elif typ=='inlineStr':value=''.join(x.text or '' for x in node.findall('.//s:is//s:t',NS))
        else:value=raw
        kind={'s':'text','inlineStr':'text','str':'text','b':'boolean','e':'error','d':'date','n':'number'}.get(typ,'unknown')
        if formula_node is not None:kind='formula'
        elif value=='':kind='blank'
        style_index=node.get('s','0')
        format_id,format_code=(None,None)
        if re.fullmatch(r'[0-9]{1,6}',style_index) and int(style_index)<len(formats):
            format_id,format_code=formats[int(style_index)]
        cells.append(cell(address,row,column,value,kind,formula,value_node.text if formula_node is not None and value_node is not None else None,
            raw_value=raw,style_index=style_index,number_format_id=format_id,number_format=format_code,formula_type=None if formula_node is None else formula_node.get('t','normal'),
            formula_shared_index=None if formula_node is None else formula_node.get('si')))
        if len(cells)>limits.max_cells:raise DocumentError('Spreadsheet cell limit exceeded')
    merged=[node.get('ref') for node in tree.findall('s:mergeCells/s:mergeCell',NS)]
    raw='\n'.join(c['address']+'\t'+c['value'] for c in cells)
    return page_result(number,'sheet','ooxml-sheet',[element(number,1,'table',raw,
        locator={'sheet':title,'cells':[c['address'] for c in cells]},cells=cells,merged_ranges=merged)],warnings=[
        'Raw stored values are strings. Effective number-format metadata is retained where recognized; dates and displayed values are not rendered.',
        'Formulas are never evaluated. cached_value may be absent or stale; shared formulas are not expanded.',
        'Only explicit stored cells are emitted; absent cells and images/charts are not inferred.'])


def pptx_page(archive, part, number, limits):
    tree=archive.tree(part);elements=[]
    for shape_index,shape in enumerate(tree.findall('.//p:spTree/*',NS),1):
        paragraphs=shape.findall('.//a:p',NS)
        tables=shape.findall('.//a:tbl',NS)
        if tables:
            for table_index,table in enumerate(tables,1):
                cells=[];lines=[]
                for r,row in enumerate(table.findall('a:tr',NS),1):
                    values=[]
                    for c,entry in enumerate(row.findall('a:tc',NS),1):
                        value='\n'.join(text_runs(p,NS['a']) for p in entry.findall('.//a:p',NS))
                        cells.append(cell('R%dC%d'%(r,c),r,c,value));values.append(value)
                        if len(cells)>limits.max_cells:raise DocumentError('Slide table cell limit exceeded')
                    lines.append('\t'.join(values))
                elements.append(element(number,len(elements)+1,'table','\n'.join(lines),
                    locator={'slide':number,'shape':shape_index,'table':table_index},cells=cells))
        elif paragraphs:
            raw='\n'.join(text_runs(p,NS['a']) for p in paragraphs)
            elements.append(element(number,len(elements)+1,'text',raw,locator={'slide':number,'shape':shape_index}))
        elif shape.tag=='{'+NS['p']+'}pic':
            elements.append(element(number,len(elements)+1,'image','',locator={'slide':number,'shape':shape_index},
                quality={'method':'office-xml','readable':False,'confidence':None}))
        elif shape.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart'):
            elements.append(element(number,len(elements)+1,'chart','',locator={'slide':number,'shape':shape_index},
                quality={'method':'office-xml','readable':False,'confidence':None}))
        if len(elements)>limits.max_elements:raise DocumentError('Slide element limit exceeded')
    return page_result(number,'slide','ooxml-slide',elements,warnings=[
        'Slide structure only; rendering, grouped-shape layout, notes, masters and image/chart text are not interpreted.',
        'Element bounding boxes are null because this extractor does not reconstruct slide transforms.'])


def image_preview(data, output, limits):
    from PIL import Image, ImageOps
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in {'PNG','JPEG','WEBP'} or getattr(original,'n_frames',1)!=1:
                    raise DocumentError('Only static PNG, JPEG and WebP images are accepted')
                if original.width*original.height>limits.max_pixels:
                    raise DocumentError('Image pixel limit exceeded')
                original.load()
                picture=ImageOps.exif_transpose(original).convert('RGBA')
                # The preview is normalized; the caller retains the original bytes.
                preview=Image.new('RGB',picture.size,'white')
                preview.paste(picture,mask=picture.getchannel('A'))
                preview.save(output/'page.png',format='PNG')
                return preview.size
    except (Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise DocumentError('Image pixel limit exceeded') from exc
    except (OSError,ValueError) as exc:
        if isinstance(exc,DocumentError):raise
        raise DocumentError('Damaged image') from exc


def normalized_box(box,width,height):
    if len(box)!=4 or not all(math.isfinite(x) for x in box) or width<=0 or height<=0:
        return None
    x0,y0,x1,y1=box
    if x1<=x0 or y1<=y0:return None
    result=[max(0.,min(1.,x0/width)),max(0.,min(1.,y0/height)),
            max(0.,min(1.,x1/width)),max(0.,min(1.,y1/height))]
    return result if result[2]>result[0] and result[3]>result[1] else None


def box_overlap(a,b):
    if not a or not b:return 0.
    area=max(0.,min(a[2],b[2])-max(a[0],b[0]))*max(0.,min(a[3],b[3])-max(a[1],b[1]))
    return area/max((a[2]-a[0])*(a[3]-a[1]),1e-12)


def parse_tsv(tsv, number, width, height, limits, excluded_boxes=()):
    if len(tsv.encode('utf-8'))>limits.max_extracted_bytes:
        raise DocumentError('OCR TSV exceeds extraction limit')
    fields=['level','page_num','block_num','par_num','line_num','word_num','left','top','width','height','conf','text']
    first=tsv.split('\n',1)[0].rstrip('\r').split('\t')
    has_header=first==fields
    if not has_header and (len(first)!=12 or not re.fullmatch(r'[1-5]',first[0])):
        raise DocumentError('OCR TSV has no valid header or first data row')
    reader=csv.DictReader(io.StringIO(tsv),fieldnames=None if has_header else fields,delimiter='\t',quoting=csv.QUOTE_NONE)
    groups={};discarded=0;word_count=0
    for row in reader:
        if None in row or any(value is None for value in row.values()):raise DocumentError('OCR TSV column count is invalid')
        if row.get('level')!='5' or not row.get('text'):continue
        try:
            left,top,w,h=[int(row[key]) for key in ('left','top','width','height')]
            confidence=float(row['conf'])
            indices=tuple(int(row[key]) for key in ('block_num','par_num','line_num'))
            word_number=int(row['word_num'])
        except (TypeError,ValueError) as exc:raise DocumentError('Malformed OCR word record') from exc
        if min(indices)<0 or word_number<1 or not math.isfinite(confidence) or not -1<=confidence<=100 or min(left,top,w,h)<0 or left+w>width+1 or top+h>height+1:
            raise DocumentError('OCR coordinates or confidence outside bounds')
        box=normalized_box([left,top,left+w,top+h],width,height)
        if any(box_overlap(box,native)>.30 for native in excluded_boxes):
            discarded+=1;continue
        item={'text':row['text'],'confidence':confidence,'bbox':box,'word_num':word_number}
        groups.setdefault(indices,[]).append(item);word_count+=1
        if word_count>limits.max_elements:
            raise DocumentError('OCR word limit exceeded')
    elements=[]
    for indices,words in groups.items():
        valid=[word['bbox'] for word in words if word['bbox']]
        box=[min(x[0] for x in valid),min(x[1] for x in valid),max(x[2] for x in valid),max(x[3] for x in valid)] if valid else None
        raw=' '.join(word['text'] for word in words)
        low=[word for word in words if word['confidence']<40]
        quality={'method':'tesseract-tsv','confidence':sum(word['confidence'] for word in words)/len(words),
                 'confidence_scale':'Tesseract 0..100 engine score, not probability or measured accuracy',
                 'readable':not low,'review_required':True,'assessment':'engine_signal_only','low_confidence_words':len(low),'words':words}
        item=element(number,len(elements)+1,'text',raw,box,{'ocr_block':indices[0],'ocr_paragraph':indices[1],'ocr_line':indices[2]},quality)
        if low:
            item['text']=' '.join(word['text'] if word['confidence']>=40 else '[unreadable]' for word in words)
            item['warnings']=['Low engine-confidence words are unreadable candidates; inspect the original preview. Raw OCR is preserved separately.']
        elements.append(item)
    return elements,discarded


def run_ocr(output, language, limits):
    script=ROOT/'scripts'/'document_ocr.mjs'
    runtime=os.environ.get('RIWAQ_DOCUMENT_RUNTIME')
    if not script.is_file() or not runtime:
        return {'success':False,'warnings':['Local OCR runtime is not configured; no text was invented.']}
    node=os.environ.get('RIWAQ_DOCUMENT_NODE','node')
    result_path=output/'ocr.json'
    if result_path.exists():result_path.unlink()
    environment={key:value for key,value in os.environ.items() if key.upper() in {
        'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','RIWAQ_DOCUMENT_RUNTIME','RIWAQ_DOCUMENT_NODE'}}
    environment.update(OMP_THREAD_LIMIT='1',OMP_NUM_THREADS='1')
    try:
        completed=subprocess.run([node,str(script),'--input',str(output/'page.png'),'--output',str(result_path),
            '--languages',languages(language)],cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=limits.ocr_timeout_seconds,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,check=False)
        if not result_path.is_file():
            return {'success':False,'warnings':['Local OCR produced no report; inspect the preview.']}
        if result_path.stat().st_size>limits.max_extracted_bytes*2:
            raise DocumentError('OCR output exceeds limit')
        value=json.loads(result_path.read_text(encoding='utf-8'))
        if not isinstance(value,dict):
            return {'success':False,'warnings':['Local OCR returned no valid report.']}
        if completed.returncode or not value.get('success') or not isinstance(value.get('tsv'),str):
            return {'success':False,'warnings':['Local OCR failed: '+str(value.get('error','No valid TSV returned'))[:300]]}
        return value
    except subprocess.TimeoutExpired:
        return {'success':False,'warnings':['Local OCR exceeded its time limit; inspect the preview.']}
    except (OSError,ValueError):
        return {'success':False,'warnings':['Local OCR engine is unavailable or returned malformed data.']}


def append_ocr(page, output, limits, excluded_boxes=()):
    data=run_ocr(output,page['language'],limits)
    page['warnings'].extend(data.get('warnings',[])[:20])
    if not data.get('success'):
        page['status']='partial' if any(e.get('indexable',True) and e['text_raw'] for e in page['elements']) else 'failed'
        return page
    elements,discarded=parse_tsv(data['tsv'],page['number'],page['width'],page['height'],limits,excluded_boxes)
    offset=len(page['elements'])
    for index,item in enumerate(elements,offset+1):item['id']='p%d-e%05d'%(page['number'],index)
    for item in elements:item['indexable']=True
    page['elements'].extend(elements)
    page['warnings'].append('OCR completion does not establish full text coverage or correctness; manual review is required.')
    page['ocr']={'engine':data.get('engine','tesseract.js'),'engine_version':data.get('engine_version'),
                 'language':page['language'],'suppressed_native_overlap_words':discarded,
                 'runtime_manifest_sha256':data.get('runtime_manifest_sha256'),'tessdata_revision':data.get('tessdata_revision'),
                 'preview_sha256':data.get('input_sha256'),'elapsed_seconds':data.get('elapsed_seconds'),
                 'score_notice':'Engine confidence is not measured accuracy or probability.',
                 'text_raw':data.get('text_raw',''),'tsv_sha256':hashlib.sha256(data['tsv'].encode('utf-8')).hexdigest()}
    page['method']+='+tesseract-tsv'
    if not elements:
        page['status']='partial' if any(e.get('indexable',True) and e['text_raw'] for e in page['elements']) else 'failed'
        page['warnings'].append('No additional readable OCR text was found; blankness and unreadability are not distinguished.')
    elif any(not e['quality'].get('readable',True) for e in elements):
        page['status']='partial'
        page['warnings'].append('Some OCR words need manual inspection; [unreadable] is a placeholder, not source text.')
    return page


def pdf_open(data, limits):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:raise DocumentError('Local PDFium package is unavailable') from exc
    try:
        document=pdfium.PdfDocument(data)
        # PDFium requires a form environment before obtaining page handles or
        # even document length. Without it a filled AcroForm previews as blank.
        # No JS platform or navigation/network callbacks are supplied. XFA is
        # deliberately not loaded by this passive document reader.
        import pypdfium2.raw as raw
        form_type=document.get_formtype()
        if form_type in (raw.FORMTYPE_XFA_FULL,raw.FORMTYPE_XFA_FOREGROUND):
            document.close();raise DocumentError('XFA preview is unsupported')
        if form_type==raw.FORMTYPE_ACRO_FORM:
            document.init_forms(config=raw.FPDF_FORMFILLINFO(version=2))
        count=len(document)
        if not 1<=count<=limits.max_pages:
            document.close();raise DocumentError('PDF page count exceeds bound')
        return document
    except DocumentError:raise
    except Exception as exc:raise DocumentError('PDF could not be opened; encrypted or malformed PDFs are unsupported') from exc


def pdf_page(document, number, output, language, limits):
    import pypdfium2.raw as raw
    page=document[number-1]
    try:
        pw,ph=page.get_size()
        if not all(math.isfinite(x) and x>0 for x in (pw,ph)):
            raise DocumentError('Invalid PDF page dimensions')
        scale=min(PDF_RENDER_SCALE,math.sqrt(limits.max_pixels/(pw*ph))*.999,4096/max(pw,ph))
        bitmap=page.render(scale=scale)
        try:
            width,height=bitmap.width,bitmap.height
            if width*height>limits.max_pixels:raise DocumentError('Rendered PDF pixel limit exceeded')
            bitmap.to_pil().convert('RGB').save(output/'page.png',format='PNG')
            conversion=bitmap.get_posconv(page)
            def pdf_box(bounds):
                left,bottom,right,top=bounds
                points=[conversion.to_bitmap(x,y) for x in (left,right) for y in (bottom,top)]
                return normalized_box([min(p[0] for p in points),min(p[1] for p in points),
                                       max(p[0] for p in points),max(p[1] for p in points)],width,height)
            textpage=page.get_textpage()
            try:
                count=textpage.count_chars()
                if count>limits.max_extracted_bytes//2:raise DocumentError('PDF character count exceeds bound')
                elements=[];chars=[];boxes=[];start=0;char_bytes=0
                def flush(end):
                    nonlocal chars,boxes,start
                    if not chars:return
                    text=''.join(chars)
                    valid=[b for b in boxes if b]
                    box=[min(b[0] for b in valid),min(b[1] for b in valid),max(b[2] for b in valid),max(b[3] for b in valid)] if valid else None
                    elements.append(element(number,len(elements)+1,'text',text,box,
                        {'pdf_char_start':start,'pdf_char_end':end},
                        {'method':'pdfium-native','confidence':None,'readable':'\ufffd' not in text}))
                    chars=[];boxes=[];start=end+1
                for index in range(count):
                    code=int(raw.FPDFText_GetUnicode(textpage,index))
                    char=chr(code) if 0<code<=0x10ffff and not 0xd800<=code<=0xdfff else '\ufffd'
                    if not chars:start=index
                    chars.append(char);char_bytes+=len(char.encode('utf-8'))
                    if char_bytes>limits.max_extracted_bytes:raise DocumentError('PDF text exceeds byte limit')
                    if char not in '\r\n\t ':
                        try:boxes.append(pdf_box(textpage.get_charbox(index)))
                        except Exception:boxes.append(None)
                    if char=='\n':flush(index)
                    if len(elements)>limits.max_elements:raise DocumentError('PDF element limit exceeded')
                flush(count-1)
            finally:textpage.close()
            rtl_elements=[]
            for item in elements:
                rtl=any(unicodedata.bidirectional(char) in {'R','AL'} for char in item['text_raw'])
                item['indexable']=not rtl
                if rtl:
                    item['quality'].update(review_required=True,reading_order_verified=False)
                    rtl_elements.append(item)
            native_boxes=[e['bbox'] for e in elements if e['indexable'] and e['bbox'] and e['text_raw'].strip() and e['quality']['readable']]
            image_count=0;images=[]
            for item in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE],max_depth=5):
                image_count+=1
                if image_count>limits.max_elements:raise DocumentError('PDF image object limit exceeded')
                try:box=pdf_box(item.get_bounds())
                except Exception:box=None
                images.append(element(number,len(elements)+len(images)+1,'image','',box,
                    {'pdf_image':image_count},{'method':'pdfium-object','confidence':None,'readable':False}))
            native_text=''.join(e['text_raw'] for e in elements)
            valid_chars=sum(c.isalnum() for c in native_text)
            weak=not valid_chars or native_text.count('\ufffd')>max(1,len(native_text)*.05)
            kind='scan' if weak else ('mixed' if images else 'native')
            result=page_result(number,kind,'pdfium-native',elements+images,width,height,language,
                ['PDF reading order follows its stored character order. Tables and chart meaning are not inferred.'],
                'page.png',status='partial' if weak else 'complete',render_scale=scale)
            # A native-only page is never OCRed; mixed pages suppress OCR words that
            # overlap actual native text boxes, preserving one authoritative layer.
            if weak or images or rtl_elements:
                append_ocr(result,output,limits,native_boxes)
            if rtl_elements:
                result['warnings'].append('Native RTL glyph order is not reliable in all PDFs. Native RTL elements are retained as raw evidence but are not indexable; local OCR supplies experimental replacement text. No character reversal was applied.')
                result['native_rtl_fallback']=True
                if result['status']=='complete':result['status']='partial'
            if weak and native_text:
                result['warnings'].append('The native text layer is weak or contains replacement characters; inspect raw text and preview.')
            return result
        finally:bitmap.close()
    finally:page.close()


def extract_document(input_path, filename, output_dir, page=0, language='ara+eng+heb', limits=None):
    limits=limits or load_limits();language=languages(language)
    if type(page) is not int or page<0:raise DocumentError('Page must be a nonnegative integer')
    source=Path(input_path)
    if not source.is_file() or source.stat().st_size>limits.max_input_bytes:raise DocumentError('Input file missing or exceeds byte limit')
    with source.open('rb') as stream:data=stream.read(limits.max_input_bytes+1)
    kind,archive=sniff(data,filename,limits)
    output=Path(output_dir).resolve()
    if page:
        output.mkdir(parents=True,exist_ok=True)
    metadata={'extractor_version':VERSION,'format':kind,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
              'source_network':False,'text_normalization':'none','filename':filename,'printed_page_number':None}
    notices=[];document=None
    try:
        if kind=='pdf':
            document=pdf_open(data,limits);page_count=len(document)
            metadata['page_units']='physical_pdf_pages'
        elif kind in {'docx','xlsx','pptx'}:
            pages=office_pages(kind,archive);page_count=len(pages)
            metadata['page_units']={'docx':'logical_document_body','xlsx':'worksheets','pptx':'slides'}[kind]
            metadata['page_names']=[title for title,_ in pages]
            if kind=='docx':notices.append('DOCX pagination is not reconstructed; body content is one logical page.')
        else:
            page_count=1;metadata['page_units']='image' if kind in {'png','jpeg','webp'} else 'logical_text'
            if kind in {'png','jpeg','webp'}:
                from PIL import Image
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width*image.height>limits.max_pixels:raise DocumentError('Image pixel limit exceeded')
                        if getattr(image,'n_frames',1)!=1:raise DocumentError('Animated images are not supported')
                        metadata.update(width=image.width,height=image.height,orientation_normalized_on_preview=True)
                        image.verify()
                except (OSError,ValueError,Image.DecompressionBombError) as exc:
                    if isinstance(exc,DocumentError):raise
                    raise DocumentError('Invalid image') from exc
        if not 1<=page_count<=limits.max_pages:raise DocumentError('Document page count exceeds limit')
        result={'success':True,'page_count':page_count,'mime':MIMES[kind],'metadata':metadata,'warnings':notices}
        if not page:return bound_result(result,limits)
        if page>page_count:raise DocumentError('Page is outside document')
        if kind=='pdf':value=pdf_page(document,page,output,language,limits)
        elif kind in {'png','jpeg','webp'}:
            width,height=image_preview(data,output,limits)
            value=page_result(1,'scan','image-exif-normalized',[],width,height,language,preview='page.png')
            append_ocr(value,output,limits)
        elif kind=='docx':value=docx_page(archive,limits)
        elif kind=='xlsx':value=xlsx_page(archive,*pages[page-1],page,limits)
        elif kind=='pptx':value=pptx_page(archive,pages[page-1][1],page,limits)
        else:value=plain_page(kind,data,limits)
        result['page']=value
        return bound_result(result,limits)
    finally:
        if document is not None:document.close()
        if archive is not None:archive.zip.close()
