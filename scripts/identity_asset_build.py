"""Build native SVG paths from captured Chrome shaping and pinned local fonts.

Development tool only: accepts the two controlled PDFs from
identity_shape_capture.mjs, never arbitrary uploaded PDFs. No network/downloads.
FontTools may run from the existing visual environment; --pypdf-site-packages
can point at the existing service environment's installed pure-Python package.
"""
from __future__ import annotations
import argparse
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET

from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'frontend/public/brand'
REPORT=ROOT/'reports/identity-20261116'
NS='http://www.w3.org/2000/svg'
ET.register_namespace('',NS)

def node(tag,attrs=None,text=None):
    result=ET.Element('{'+NS+'}'+tag,attrs or {});result.text=text;return result

def svg_file(path,viewbox,title,paths,color='#126456',description=''):
    svg=node('svg',{'viewBox':viewbox,'role':'img','aria-labelledby':'title desc'})
    svg.append(node('title',{'id':'title'},title));svg.append(node('desc',{'id':'desc'},description))
    for values in paths:
        svg.append(node('path',{'fill':color,**values}))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(ET.tostring(svg,encoding='utf-8',xml_declaration=True))

def capture(name,font_name,text):
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject
    pdf=REPORT/'shaping'/f'{name}.pdf'
    if pdf.stat().st_size>100_000:raise ValueError('Controlled logo PDF exceeds limit')
    reader=PdfReader(pdf)
    if len(reader.pages)!=1:raise ValueError('Expected one controlled logo page')
    page=reader.pages[0]
    if page.extract_text().strip()!=text:raise ValueError('Shaped PDF text differs from the approved word')
    fonts=page['/Resources']['/Font']
    if len(fonts)!=1:raise ValueError('Unexpected font fallback')
    font_object=next(iter(fonts.values())).get_object();descendant=font_object['/DescendantFonts'][0].get_object()
    if font_object['/Encoding']!='/Identity-H' or descendant['/CIDToGIDMap']!='/Identity':raise ValueError('Unsupported glyph encoding')
    font=TTFont(ROOT/'frontend/public/fonts'/font_name)
    embedded=TTFont(BytesIO(descendant['/FontDescriptor']['/FontFile2'].get_data()))
    font_glyphs=font.getGlyphSet();embedded_glyphs=embedded.getGlyphSet();glyph_order=font.getGlyphOrder()
    if font['head'].unitsPerEm!=1000 or embedded['head'].unitsPerEm!=1000:raise ValueError('Unsupported font metrics')
    unicode_map={}
    for group in re.findall(r'beginbfchar(.*?)endbfchar',font_object['/ToUnicode'].get_data().decode('ascii'),re.S):
        for cid,unicode_hex in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',group):
            unicode_map[int(cid,16)]=bytes.fromhex(unicode_hex).decode('utf-16-be')
    widths={};width_list=descendant.get('/W',[]);index=0
    while index<len(width_list):
        start=int(width_list[index]);index+=1;values=width_list[index];index+=1
        if isinstance(values,ArrayObject):
            for offset,width in enumerate(values):widths[start+offset]=float(width)
        else:
            stop=int(values);width=float(width_list[index]);index+=1
            for gid in range(start,stop+1):widths[gid]=width
    default_width=float(descendant.get('/DW',1000))
    records=[];line_x=line_y=x=y=0.0;size=None
    for args,op in page.get_contents().operations:
        if op==b'Tf':
            size=float(args[1])
            if size!=1000:raise ValueError('Unexpected logo capture size')
        elif op==b'Tm':
            if [float(v) for v in args[:4]]!=[1,0,0,-1]:raise ValueError('Unexpected logo text transform')
            line_x=x=float(args[4]);line_y=y=float(args[5])
        elif op==b'Td':
            line_x+=float(args[0]);line_y-=float(args[1]);x=line_x;y=line_y
        elif op==b'Tj':
            data=args[0].original_bytes if hasattr(args[0],'original_bytes') else bytes(args[0])
            if size is None or len(data)%2:raise ValueError('Invalid logo glyph stream')
            for position in range(0,len(data),2):
                gid=int.from_bytes(data[position:position+2],'big');character=unicode_map[gid]
                glyph=glyph_order[gid]
                # Verify the PDF's actual shaped glyph outline against the pinned
                # source, rather than assuming cmap character order is shaping.
                original_pen=SVGPathPen(font_glyphs);font_glyphs[glyph].draw(original_pen)
                embedded_pen=SVGPathPen(embedded_glyphs);embedded_glyphs[embedded.getGlyphOrder()[gid]].draw(embedded_pen)
                if original_pen.getCommands()!=embedded_pen.getCommands():raise ValueError('Captured glyph differs from the pinned font')
                records.append({'glyph_id':gid,'glyph':glyph,'text':character,'x':x,'y':y})
                x+=widths.get(gid,default_width)
        elif op in {b'TJ',b'Tc',b'Tw',b'Tz',b'T*',b"'",b'"'}:raise ValueError('Unexpected shaping operator; review capture before export')
    if sorted(r['text'] for r in records)!=sorted(text):raise ValueError('Unexpected captured characters')
    paths=[];bounds=[]
    for record in records:
        pen=SVGPathPen(font_glyphs);transform=(1,0,0,-1,record['x'],record['y'])
        font_glyphs[record['glyph']].draw(TransformPen(pen,transform));paths.append({'d':pen.getCommands()})
        bound=BoundsPen(font_glyphs);font_glyphs[record['glyph']].draw(TransformPen(bound,transform));bounds.append(bound.bounds)
    bbox=[min(b[0] for b in bounds),min(b[1] for b in bounds),max(b[2] for b in bounds),max(b[3] for b in bounds)]
    return {'text':text,'font':font_name,'font_sha256':hashlib.sha256((ROOT/'frontend/public/fonts'/font_name).read_bytes()).hexdigest(),
            'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'glyphs':records,'bounds':bbox,'paths':paths},font

def box(values,pad=0):
    x1,y1,x2,y2=values
    return f'{x1-pad:g} {y1-pad:g} {x2-x1+pad*2:g} {y2-y1+pad*2:g}'

def build(canonical=False):
    geometry=json.loads((ROOT/'frontend/src/brand-geometry.json').read_text('utf-8'))
    arabic,font=capture('arabic','IBMPlexSansArabic-SemiBold.woff2','رواق')
    latin,_=capture('latin','IBMPlexSans-Medium.woff2','Riwaq')
    description='Arabic typography based on IBM Plex Sans Arabic SemiBold, OFL-1.1. Chrome-shaped glyph positions preserved as editable paths; not a custom font.'
    directory=OUT/'directions'
    svg_file(directory/'wordmark.svg',box(arabic['bounds'],8),'رواق',arabic['paths'],description=description)
    svg_file(directory/'latin-wordmark.svg',box(latin['bounds'],8),'Riwaq',latin['paths'],description='IBM Plex Sans Medium; native outlines with Chrome shaping positions preserved.')
    qaf=next(r for r in arabic['glyphs'] if r['text']=='ق')
    glyphs=font.getGlyphSet();bounds=BoundsPen(glyphs);glyphs[qaf['glyph']].draw(bounds)
    x1,y1,x2,y2=bounds.bounds;scale=min(34/(x2-x1),36/(y2-y1));cx=(x1+x2)/2;cy=(y1+y2)/2
    pen=SVGPathPen(glyphs);glyphs[qaf['glyph']].draw(TransformPen(pen,(scale,0,0,-scale,24-cx*scale,24+cy*scale)))
    paths={'a':pen.getCommands(),'b':geometry['path'],'c':geometry['direction_c']['path']}
    for key,path in paths.items():
        svg_file(directory/f'{key}-symbol.svg','0 0 48 48',f'رواق — اتجاه {key.upper()}',[{'d':path}],description='A: font-derived qaf emblem.' if key=='a' else geometry['meaning'] if key=='b' else 'C: three rhythmic independent strokes on a compact grid.')
        lockup=[{'d':path,'transform':'translate(190 41)'}]+[p|{'transform':'translate(29.4 33.9) scale(0.066)'} for p in arabic['paths']]
        svg_file(directory/f'{key}-lockup.svg','0 0 260 130','رواق',lockup,description=description+' Shared 66px typography, baseline90 and symbol placement match the v1 comparison canvas.')
    if canonical:
        svg_file(OUT/'riwaq-symbol.svg',geometry['viewBox'],'رواق — العلامة المختصرة',[{'d':geometry['path']}],color='currentColor',description=geometry['meaning'])
        for variant,color in {**{key:geometry['palette'][key] for key in ('light','dark','mono')},'black':'#000000','white':'#FFFFFF'}.items():
            svg_file(OUT/f'riwaq-symbol-{variant}.svg',geometry['viewBox'],'رواق — العلامة المختصرة',[{'d':geometry['path']}],color=color,description=geometry['meaning'])
            lockup=[{'d':geometry['path'],'transform':'translate(190 41)'}]+[p|{'transform':'translate(29.4 33.9) scale(0.066)'} for p in arabic['paths']]
            svg_file(OUT/f'riwaq-wordmark-{variant}.svg','0 0 260 130','رواق',lockup,color=color,description=description)
            svg_file(OUT/f'riwaq-lockup-{variant}.svg','0 0 260 130','رواق',lockup,color=color,description=description)
            svg_file(OUT/f'riwaq-lettering-{variant}.svg',box(arabic['bounds'],8),'رواق',arabic['paths'],color=color,description=description)
            # Arabic-primary bilingual lockup; the Latin line is supplementary.
            bilingual=[{'d':geometry['path'],'transform':'translate(190 38)'}]+[p|{'transform':'translate(29.4 20.9) scale(0.066)'} for p in arabic['paths']]
            # Use real glyph bounds, including Arabic/Latin descenders. A prior
            # 108px Latin line at bottom112 overlapped qaf: keep >=10px clearance.
            lx1,ly1,lx2,ly2=latin['bounds'];ls=72/(lx2-lx1);tx=29.4+arabic['bounds'][2]*0.066-lx2*ls;ty=130-ly2*ls
            assert ty+ly1*ls-(20.9+arabic['bounds'][3]*0.066)>=10
            bilingual += [p|{'transform':f'translate({tx:g} {ty:g}) scale({ls:g})'} for p in latin['paths']]
            svg_file(OUT/f'riwaq-bilingual-{variant}.svg','0 0 260 140','رواق · Riwaq',bilingual,color=color,description=description+' Latin: IBM Plex Sans Medium.')
    receipt={'method':'Chrome-shaped PDF CID/glyph positions → pinned FontTools glyph outlines; embedded/source outlines checked equal',
             'canonical_exported':canonical,'selected_direction':geometry.get('selected_direction'),'geometry_sha256':hashlib.sha256((ROOT/'frontend/src/brand-geometry.json').read_bytes()).hexdigest(),
             'arabic':{k:v for k,v in arabic.items() if k!='paths'},'latin':{k:v for k,v in latin.items() if k!='paths'}}
    (REPORT/'vector-build.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'directions':3,'canonical':canonical,'arabic_glyphs':len(arabic['glyphs']),'latin_glyphs':len(latin['glyphs']),'output':str(directory)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pypdf-site-packages',type=Path);parser.add_argument('--canonical',action='store_true');args=parser.parse_args()
    if args.pypdf_site_packages:
        source=args.pypdf_site_packages.resolve()
        if not (source/'pypdf/__init__.py').is_file():parser.error('pypdf was not found at the supplied installed package directory')
        sys.path.append(str(source))
    build(args.canonical)
