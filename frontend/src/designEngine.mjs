const FAMILIES={arabic:'IBM Plex Sans Arabic',latin:'IBM Plex Sans',hebrew:'IBM Plex Sans Hebrew'};
const latest=new WeakMap(),HEX=/^#[0-9a-f]{6}$/i,white=/^\p{White_Space}$/u;
const finite=n=>typeof n==='number'&&Number.isFinite(n);
const fail=c=>{throw Error(c);};
const sha=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
const rect=r=>({left:r.x,top:r.y,right:r.x+r.width,bottom:r.y+r.height});
const contains=(a,b)=>b.left>=a.left&&b.top>=a.top&&b.right<=a.right&&b.bottom<=a.bottom;
const cp=c=>'U+'+c.codePointAt(0).toString(16).toUpperCase().padStart(4,'0');
export function perspectiveMap(q,width,height){
 if(!Array.isArray(q)||q.length!==4||q.some(p=>!Array.isArray(p)||p.length!==2||p.some(n=>!finite(n)||n<0))||q.some(p=>p[0]>width||p[1]>height))fail('invalid_text_plane');
 for(let i=0;i<4;i++){const a=q[i],b=q[(i+1)%4],c=q[(i+2)%4];if((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])<16)fail('invalid_text_plane');}
 const [p0,p1,p2,p3]=q,dx1=p1[0]-p2[0],dx2=p3[0]-p2[0],dx3=p0[0]-p1[0]+p2[0]-p3[0],dy1=p1[1]-p2[1],dy2=p3[1]-p2[1],dy3=p0[1]-p1[1]+p2[1]-p3[1],det=dx1*dy2-dx2*dy1;
 if(Math.abs(det)<1e-6)fail('degenerate_text_plane');
 const g=(dx3*dy2-dx2*dy3)/det,h=(dx1*dy3-dx3*dy1)/det;
 if([1,1+g,1+h,1+g+h].some(n=>n<=1e-6))fail('folded_text_plane');
 const a=p1[0]-p0[0]+g*p1[0],b=p3[0]-p0[0]+h*p3[0],d=p1[1]-p0[1]+g*p1[1],e=p3[1]-p0[1]+h*p3[1];
 return (u,v)=>[(a*u+b*v+p0[0])/(g*u+h*v+1),(d*u+e*v+p0[1])/(g*u+h*v+1)];
}
function mappedBounds(box,l,map){const q=[[box.left,box.top],[box.right,box.top],[box.right,box.bottom],[box.left,box.bottom]].map(p=>map((p[0]-l.x)/l.width,(p[1]-l.y)/l.height));return {left:Math.min(...q.map(p=>p[0])),right:Math.max(...q.map(p=>p[0])),top:Math.min(...q.map(p=>p[1])),bottom:Math.max(...q.map(p=>p[1]))};}
function drawPlane(ctx,source,l,map){
 const n=24;
 function triangle(s,t){
  const ux=s[1][0]-s[0][0],uy=s[1][1]-s[0][1],vx=s[2][0]-s[0][0],vy=s[2][1]-s[0][1],det=ux*vy-vx*uy,dx=t[1][0]-t[0][0],dy=t[1][1]-t[0][1],ex=t[2][0]-t[0][0],ey=t[2][1]-t[0][1];
  const a=(dx*vy-ex*uy)/det,b=(dy*vy-ey*uy)/det,c=(ex*ux-dx*vx)/det,d=(ey*ux-dy*vx)/det;
  ctx.save();ctx.beginPath();ctx.moveTo(...t[0]);ctx.lineTo(...t[1]);ctx.lineTo(...t[2]);ctx.closePath();ctx.clip();ctx.setTransform(a,b,c,d,t[0][0]-a*s[0][0]-c*s[0][1],t[0][1]-b*s[0][0]-d*s[0][1]);ctx.drawImage(source,0,0);ctx.restore();
 }
 for(let y=0;y<n;y++)for(let x=0;x<n;x++){const uv=[[x/n,y/n],[(x+1)/n,y/n],[(x+1)/n,(y+1)/n],[x/n,(y+1)/n]],s=uv.map(p=>[p[0]*l.width,p[1]*l.height]),t=uv.map(p=>map(...p));triangle([s[0],s[1],s[2]],[t[0],t[1],t[2]]);triangle([s[0],s[2],s[3]],[t[0],t[2],t[3]]);}
}
function validate(s){
 if(!s||!Number.isInteger(s.width)||!Number.isInteger(s.height)||s.width<256||s.height<256||s.width>2048||s.height>2048||s.width*s.height>4000000)fail('invalid_canvas');
 if(s.background!=='transparent'&&!HEX.test(s.background))fail('invalid_background');
 if(!Array.isArray(s.layers)||s.layers.length>16||!Array.isArray(s.shapes)||s.shapes.length>32)fail('layer_limit');
 const ids=new Set();let chars=0;
 for(const r of [...s.layers,...s.shapes]){if(typeof r.id!=='string'||ids.has(r.id)||!r.id)fail('invalid_id');ids.add(r.id);if(!['x','y','width','height'].every(k=>finite(r[k]))||r.x<0||r.y<0||r.width<1||r.height<1||r.x+r.width>s.width||r.y+r.height>s.height)fail('invalid_geometry');}
 for(const r of s.shapes)if(!['rect','ellipse'].includes(r.kind)||!HEX.test(r.fill))fail('invalid_shape');
 for(const l of s.layers){
  if(l.perspective!=null)perspectiveMap(l.perspective,s.width,s.height);
  if(typeof l.text!=='string'||[...l.text].length>4000)fail('text_limit');chars+=[...l.text].length;
  if([...l.text].some(c=>{const n=c.codePointAt(0);return n>=0xd800&&n<=0xdfff;}))fail('invalid_unicode');
  if(!Object.hasOwn(FAMILIES,l.font)||![400,600].includes(l.weight)||!finite(l.size)||l.size<8||l.size>256||!HEX.test(l.color)||!['ar','en','es','he'].includes(l.language)||!['ltr','rtl','auto'].includes(l.direction)||!['left','center','right'].includes(l.align)||l.protected!==true)fail('invalid_text_layer');
 }if(chars>16000)fail('total_text_limit');
}
function validManifest(e,f){
 if(!e||e.family!==f||!/^[a-f0-9]{64}$/i.test(e.sha256||'')||!Array.isArray(e.ranges)||e.ranges.length>25000)return false;
 let prev=-1;return e.ranges.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isInteger)&&p[0]>prev&&p[0]>=0&&p[1]>=p[0]&&p[1]<=0x10ffff&&(prev=p[1])>=0);
}
function covers(r,n){let lo=0,hi=r.length-1;while(lo<=hi){const m=(lo+hi)>>1;if(n<r[m][0])hi=m-1;else if(n>r[m][1])lo=m+1;else return true;}return false;}
function direction(text,requested){
 if(requested!=='auto')return requested;
 const h=document.createElement('div'),p=document.createElement('span');h.dir='ltr';h.style.cssText='position:fixed;left:-99999px;visibility:hidden';h.setAttribute('aria-hidden','true');p.dir='auto';p.textContent=text;h.append(p);document.body.append(h);try{return getComputedStyle(p).direction==='rtl'?'rtl':'ltr';}finally{h.remove();}
}
async function fontInfo(l,coverage){
 const keys=[...new Set([l.font,'arabic','latin','hebrew'])],faces=[];
 for(const key of keys){
  const family=FAMILIES[key],e=coverage[key+':'+l.weight];
  if(!validManifest(e,family))continue;
  const loaded=await document.fonts.load(l.weight+' '+l.size+'px "'+family+'"',l.text||' ');
  const ok=loaded.some(f=>f.status==='loaded'&&f.family.replace(/^['"]|['"]$/g,'')===family&&String(f.weight)===String(l.weight));
  if(ok)faces.push({key,family,sha256:e.sha256,ranges:e.ranges});
 }
 const missing=[],fallbacks=[];
 for(const c of new Set([...l.text])){
  if(white.test(c))continue;
  const found=faces.find(f=>covers(f.ranges,c.codePointAt(0)));
  if(!found)missing.push(cp(c));else if(found.key!==l.font)fallbacks.push({codepoint:cp(c),family:found.family});
 }
 const unsupported=[...new Set([...l.text])].filter(c=>{const n=c.codePointAt(0);return ((n<32||n>=127&&n<160)&&![9,10,13].includes(n))||[0x061c,0x200e,0x200f].includes(n)||n>=0x202a&&n<=0x202e||n>=0x2066&&n<=0x2069;}).map(cp);
 return {primary_loaded:faces.some(f=>f.key===l.font),stack:faces.map(f=>'"'+f.family+'"').join(','),faces:faces.map(({ranges,...f})=>f),missing_codepoints:missing,fallbacks,unsupported_controls:unsupported};
}
async function raster(data){
 if(typeof data!=='string'||data.length>22000100||!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+$/.test(data))fail('invalid_asset');
 const im=new Image();im.src=data;await im.decode();if(im.naturalWidth*im.naturalHeight>4000000)fail('asset_pixels');return im;
}
function wrap(text,l,ctx,segmenter){
 const result=[],breaks=/\r\n|[\r\n\u2028\u2029]/g;let at=0,m;
 function append(end,separator){
  const paragraph=text.slice(at,end),dir=direction(paragraph,l.direction),g=[...segmenter.segment(paragraph)];ctx.direction=dir;
  if(!g.length)result.push({text:'',separator,start:at,end:at,direction:dir});
  else{
   let i=0;
   while(i<g.length){
    if(result.length>=256)fail('line_limit');
    let lo=i+1,hi=g.length,best=i;
    while(lo<=hi){const mid=(lo+hi)>>1,e=mid<g.length?g[mid].index:paragraph.length;if(ctx.measureText(paragraph.slice(g[i].index,e)).width<=l.width){best=mid;lo=mid+1;}else hi=mid-1;}
    if(best===i)fail('word_too_wide');
    let stop=best;
    if(best<g.length){
     let boundary=/^[ \t]+$/.test(g[best].segment);
     for(let j=best-1;j>=i;j--)if(/^[ \t]+$/.test(g[j].segment)){stop=j+1;boundary=true;break;}
     if(!boundary)fail('word_too_wide');
    }
    const begin=g[i].index,e=stop<g.length?g[stop].index:paragraph.length;
    result.push({text:paragraph.slice(begin,e),separator:'',start:at+begin,end:at+e,direction:dir});i=stop;
   }result[result.length-1].separator=separator;
  }
 }
 while((m=breaks.exec(text))!==null){append(m.index,m[0]);at=m.index+m[0].length;}append(text.length,'');
 if(result.length>256)fail('line_limit');return result;
}
export async function renderDesign(canvas,spec,assets={},fontCoverage={}){
 if(!crypto.subtle||!document.fonts||!Intl.Segmenter)fail('browser_capability_missing');
 const token={};latest.set(canvas,token);const s=structuredClone(spec);validate(s);
 const work=document.createElement('canvas');work.width=s.width;work.height=s.height;const main=work.getContext('2d',{alpha:true,colorSpace:'srgb'});if(!main)fail('canvas_unavailable');const ctx=main;
 const fonts=await Promise.all(s.layers.map(l=>fontInfo(l,fontCoverage))),bounds={left:0,top:0,right:s.width,bottom:s.height};
 if(s.background!=='transparent'){ctx.fillStyle=s.background;ctx.fillRect(0,0,s.width,s.height);}
 if(s.background_asset_id){if(!Object.hasOwn(assets,s.background_asset_id))fail('missing_background');const im=await raster(assets[s.background_asset_id]),k=Math.min(s.width/im.naturalWidth,s.height/im.naturalHeight);ctx.drawImage(im,(s.width-im.naturalWidth*k)/2,(s.height-im.naturalHeight*k)/2,im.naturalWidth*k,im.naturalHeight*k);}
 const shapes=s.shapes.map(r=>{ctx.fillStyle=r.fill;ctx.beginPath();if(r.kind==='rect')ctx.rect(r.x,r.y,r.width,r.height);else ctx.ellipse(r.x+r.width/2,r.y+r.height/2,r.width/2,r.height/2,0,0,Math.PI*2);ctx.fill();return {id:r.id,bounds:rect(r),inside_canvas:contains(bounds,rect(r))};});
 const layers=[],segmenter=new Intl.Segmenter('und',{granularity:'grapheme'});
 for(let i=0;i<s.layers.length;i++){
  const l=s.layers[i],font=fonts[i],r={id:l.id,text:l.text,source_text_sha256:await sha(l.text),font,layer_bounds:rect(l),lines:[],overflow:[],unchanged:false,metrics_available:true,ink_bounds:null};
  const map=l.perspective?perspectiveMap(l.perspective,s.width,s.height):null,plane=map?document.createElement('canvas'):null;
  if(plane){plane.width=Math.ceil(l.width);plane.height=Math.ceil(l.height);}
  const ctx=plane?plane.getContext('2d',{alpha:true,colorSpace:'srgb'}):main;if(!ctx)fail('canvas_unavailable');
  ctx.save();ctx.font=l.weight+' '+l.size+'px '+(font.stack||'"'+FAMILIES[l.font]+'"');ctx.textAlign=l.align;ctx.textBaseline='alphabetic';ctx.fillStyle=l.color;
  if(plane)ctx.translate(-l.x,-l.y);
  if('letterSpacing'in ctx)ctx.letterSpacing='0px';if('wordSpacing'in ctx)ctx.wordSpacing='0px';if('lang'in ctx)ctx.lang=l.language;
  try{
   const lines=wrap(l.text,l,ctx,segmenter),metrics=lines.map(line=>{ctx.direction=line.direction;return ctx.measureText(line.text);});
   const ascent=Math.max(l.size,...metrics.map(m=>Math.max(m.actualBoundingBoxAscent||0,m.fontBoundingBoxAscent||l.size))),descent=Math.max(l.size*.25,...metrics.map(m=>Math.max(m.actualBoundingBoxDescent||0,m.fontBoundingBoxDescent||0))),step=Math.max(l.size*1.5,ascent+descent);
   r.needed_height=(lines.length-1)*step+ascent+descent;r.line_height=step;
   if(r.needed_height>l.height)r.overflow.push({code:'text_height',required:r.needed_height,available:l.height});
   const anchor=l.x+(l.align==='center'?l.width/2:l.align==='right'?l.width:0);
   for(let j=0;j<lines.length;j++){
    const line=lines[j],m=metrics[j],y=l.y+ascent+j*step,good=['actualBoundingBoxLeft','actualBoundingBoxRight','actualBoundingBoxAscent','actualBoundingBoxDescent'].every(k=>finite(m[k]));
    if(!good)r.metrics_available=false;
    let x=anchor;
    if(good){const min=l.x+m.actualBoundingBoxLeft,max=l.x+l.width-m.actualBoundingBoxRight;if(min<=max)x=Math.max(min,Math.min(max,x));else r.overflow.push({line:j,code:'ink_width',required:m.actualBoundingBoxLeft+m.actualBoundingBoxRight,available:l.width});}
    const ink={left:x-m.actualBoundingBoxLeft,top:y-m.actualBoundingBoxAscent,right:x+m.actualBoundingBoxRight,bottom:y+m.actualBoundingBoxDescent},hasInk=m.actualBoundingBoxLeft+m.actualBoundingBoxRight>0&&m.actualBoundingBoxAscent+m.actualBoundingBoxDescent>0;
    if(m.width>l.width)r.overflow.push({line:j,code:'advance_width'});
    if(hasInk&&(!contains(rect(l),ink)||!contains(bounds,ink)))r.overflow.push({line:j,code:'ink_outside'});
    if(hasInk){const p=r.ink_bounds;r.ink_bounds=p?{left:Math.min(p.left,ink.left),top:Math.min(p.top,ink.top),right:Math.max(p.right,ink.right),bottom:Math.max(p.bottom,ink.bottom)}:ink;}
    ctx.direction=line.direction;ctx.fillText(line.text,x,y);r.lines.push({...line,x,baseline_y:y,advance:m.width,ink_bounds:hasInk?ink:null});
   }
   const reconstructed=r.lines.map(line=>line.text+line.separator).join('');r.unchanged=reconstructed===l.text;r.reconstructed_text_sha256=await sha(reconstructed);
  }catch(e){r.layout_error=String(e).slice(0,300);if(e instanceof Error&&e.message==='word_too_wide')r.overflow.push({code:'word_too_wide'});}finally{ctx.restore();}
  r.structural_success=r.unchanged&&r.metrics_available&&!r.layout_error&&!r.overflow.length&&font.primary_loaded&&!font.missing_codepoints.length&&!font.unsupported_controls.length;
  if(map){
   r.perspective={corners:l.perspective,method:'projective_triangle_mesh',grid:24,visual_quality_verified:false};
   if(r.ink_bounds)r.ink_bounds=mappedBounds(r.ink_bounds,l,map);
   for(const line of r.lines)if(line.ink_bounds)line.ink_bounds=mappedBounds(line.ink_bounds,l,map);
   if(r.structural_success)drawPlane(main,plane,l,map);
  }
  layers.push(r);
 }
 const overlap=[],ink=layers.filter(l=>l.ink_bounds);
 for(let i=0;i<ink.length;i++)for(let j=i+1;j<ink.length;j++){const touching=ink[i].lines.some(x=>x.ink_bounds&&ink[j].lines.some(y=>{if(!y.ink_bounds)return false;const a=x.ink_bounds,b=y.ink_bounds;return Math.min(a.right,b.right)>Math.max(a.left,b.left)&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top);}));if(touching)overlap.push([ink[i].id,ink[j].id]);}
 const cancelled=latest.get(canvas)!==token,report={renderer:'riwaq-canvas-literal-v1',width:s.width,height:s.height,cancelled,success:!cancelled&&layers.every(l=>l.structural_success)&&shapes.every(r=>r.inside_canvas)&&!overlap.length,success_scope:'Source, cmap and geometry checks; not OCR or semantic/visual quality.',source_text_sha256:Object.fromEntries(layers.map(l=>[l.id,l.source_text_sha256])),source_unchanged:layers.every(l=>l.unchanged),layers,shapes,overlap,background_fit:'contain',ocr:{available:false,performed:false},remaining:['Review Arabic shaping, diacritics, mixed direction and visual legibility.','No pixel OCR or semantic image check performed.','sRGB browser raster; no print color-profile certification.']};
 if(!cancelled){canvas.width=work.width;canvas.height=work.height;canvas.getContext('2d').drawImage(work,0,0);}
 return {canvas,report};
}
