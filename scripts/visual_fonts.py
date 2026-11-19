import hashlib,json
from pathlib import Path
from fontTools.ttLib import TTFont
ROOT=Path(__file__).resolve().parents[1];folder=ROOT/'frontend/public/fonts';result={}
for key,prefix,family in [('arabic','IBMPlexSansArabic','IBM Plex Sans Arabic'),('latin','IBMPlexSans','IBM Plex Sans'),('hebrew','IBMPlexSansHebrew','IBM Plex Sans Hebrew')]:
 for weight,suffix in [(400,'Regular'),(600,'SemiBold')]:
  name=prefix+'-'+suffix+'.woff2';path=folder/name;face=TTFont(path);cmap=face.getBestCmap();points=sorted(n for n,g in cmap.items() if g!='.notdef' and face.getGlyphID(g)!=0);ranges=[]
  for n in points:
   if ranges and n==ranges[-1][1]+1:ranges[-1][1]=n
   else:ranges.append([n,n])
  result[key+':'+str(weight)]={'family':family,'file':name,'weight':weight,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'ranges':ranges,'cmap_codepoints':len(points)}
(folder/'visual-coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
p=ROOT/'frontend/src/fonts.css';css=p.read_text(encoding='utf-8')
for weight,suffix in [(400,'Regular'),(600,'SemiBold')]:
 rule='@font-face{font-family:"IBM Plex Sans Hebrew";src:url("/fonts/IBMPlexSansHebrew-'+suffix+'.woff2") format("woff2");font-style:normal;font-weight:'+str(weight)+';font-display:swap;}'
 if rule not in css:css+='\n'+rule+'\n'
p.write_text(css,encoding='utf-8')
print(json.dumps({k:{'glyphs':v['cmap_codepoints'],'shekel':any(a<=0x20aa<=b for a,b in v['ranges'])} for k,v in result.items()}))
