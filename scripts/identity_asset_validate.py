"""Read-only validation of local brand assets; writes one bounded evidence JSON."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
from xml.etree import ElementTree as ET
from PIL import Image, __version__ as pillow_version

ROOT=Path(__file__).resolve().parents[1]
BRAND=ROOT/'frontend/public/brand'
REPORT=ROOT/'reports/identity-20261116'
NS='{http://www.w3.org/2000/svg}'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    geometry=json.loads((ROOT/'frontend/src/brand-geometry.json').read_text('utf-8'))
    ui=(ROOT/'frontend/src/ui.tsx').read_text('utf-8')
    assert "from './brand-geometry.json'" in ui and 'd={brandGeometry.path}' in ui and 'viewBox={brandGeometry.viewBox}' in ui
    records=[]
    for path in sorted(BRAND.rglob('*.svg')):
        raw=path.read_bytes();assert len(raw)<100_000
        svg=ET.fromstring(raw);assert svg.tag==NS+'svg'
        for item in svg.iter():
            assert item.tag in {NS+'svg',NS+'title',NS+'desc',NS+'path'}, (path.name,item.tag)
            assert not any(name.startswith('on') or name.endswith('href') or name in {'style'} for name in item.attrib)
            assert all('url(' not in value and 'data:' not in value for value in item.attrib.values())
        paths=svg.findall(NS+'path');assert paths and all(p.attrib['d'] for p in paths)
        viewbox=[float(v) for v in svg.attrib['viewBox'].split()];assert len(viewbox)==4 and viewbox[2]>0 and viewbox[3]>0
        if path.name.startswith('riwaq-symbol') or path.name=='b-symbol.svg':
            assert len(paths)==1 and paths[0].attrib['d']==geometry['path'] and svg.attrib['viewBox']==geometry['viewBox']
        if path.name.startswith('riwaq-wordmark') or path.name.startswith('riwaq-lockup'):
            assert len(paths)==5 and paths[0].attrib['d']==geometry['path'] and viewbox==[0,0,260,130]
        if path.name.startswith('riwaq-lettering'):assert len(paths)==4
        if path.name.startswith('riwaq-bilingual'):assert len(paths)==10 and viewbox==[0,0,260,140]
        records.append({'file':path.relative_to(ROOT).as_posix(),'paths':len(paths),'viewBox':viewbox,'sha256':digest(path)})
    pngs=[]
    for size in (16,24,32,48,64,128,256):
        for name in (f'icon-{size}.png',f'icon-transparent-{size}.png'):
            path=BRAND/name
            with Image.open(path) as image:
                image.load();assert image.mode=='RGBA' and image.size==(size,size)
                alpha=image.getchannel('A');assert alpha.getextrema()==(0,255)
                assert image.getpixel((0,0))[3]==0
                pngs.append({'file':path.relative_to(ROOT).as_posix(),'size':image.size,'alphaRange':alpha.getextrema(),'nontransparentBounds':alpha.getbbox(),'sha256':digest(path)})
    desktop=ROOT/'desktop/src-tauri/icons'
    ico=(desktop/'icon.ico').read_bytes();reserved,kind,count=struct.unpack_from('<HHH',ico);assert (reserved,kind,count)==(0,1,7)
    frames=[]
    for index,size in enumerate((32,16,24,48,64,128,256)):
        w,h,colors,unused,planes,bits,length,offset=struct.unpack_from('<BBBBHHII',ico,6+index*16)
        assert (w or 256,h or 256)==(size,size) and (planes,bits)==(1,32)
        data=ico[offset:offset+length];assert len(data)==length and data==(BRAND/f'icon-{size}.png').read_bytes()
        with Image.open(BytesIO(data)) as image:image.load();assert image.size==(size,size) and image.mode=='RGBA'
        frames.append({'size':size,'bits':bits,'offset':offset,'bytes':length})
    with Image.open(desktop/'icon.ico') as image:
        assert image.ico.sizes()=={(s,s) for s in (16,24,32,48,64,128,256)}
        for size in image.ico.sizes():image.ico.getimage(size).load()
    for size in (32,128,256):assert digest(desktop/f'{size}x{size}.png')==digest(BRAND/f'icon-{size}.png')
    assert digest(desktop/'icon.png')==digest(BRAND/'icon-256.png')
    manifest=json.loads((ROOT/'frontend/public/fonts/manifest.json').read_text('utf-8'))
    for item in manifest['files']:assert digest(ROOT/'frontend/public/fonts'/item['file'])==item['sha256']
    proof=json.loads((REPORT/'vector-build.json').read_text('utf-8'))
    assert proof['canonical_exported'] and proof['selected_direction']=='B'
    assert proof['geometry_sha256']==digest(ROOT/'frontend/src/brand-geometry.json')
    result={'passed':True,'environment':'Windows x64; existing Python virtualenv; local files only','pillow':pillow_version,
      'selectedDirection':'B','uiUsesCanonicalGeometry':True,'svgPassiveNativePathsOnly':True,'svgCount':len(records),'svg':records,'png':pngs,'icoFrames':frames,
      'fontManifestFilesVerified':len(manifest['files']),'fontCommit':manifest['commit'],'glyphProof':'vector-build.json',
      'visualReview':'vector-directions-preview.png inspected at 16/24/32 CSS px; independent native Windows app display not tested',
      'limits':['No trademark clearance','No OS icon cache or signed installer validation','Outlines retain editable nodes; they are not live editable text']}
    (REPORT/'asset-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'passed':True,'svgCount':len(records),'pngCount':len(pngs),'icoFrames':len(frames),'fontHashes':len(manifest['files'])}))

if __name__=='__main__':main()
