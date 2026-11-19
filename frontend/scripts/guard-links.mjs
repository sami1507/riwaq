import {readFile,writeFile,copyFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../package.json',import.meta.url));
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server'),ts=require('typescript');
const {default:Markdown}=await import(pathToFileURL(require.resolve('react-markdown')).href);
const helperSource=await readFile(new URL('../src/safe-links.ts',import.meta.url),'utf8');
const componentSource=await readFile(new URL('../src/SourceLink.tsx',import.meta.url),'utf8');
const options={target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,jsx:ts.JsxEmit.React};
const moduleUrl=code=>'data:text/javascript;base64,'+Buffer.from(code).toString('base64');
const helperUrl=moduleUrl(ts.transpileModule(helperSource,{compilerOptions:options}).outputText);
const componentCode='import React from '+JSON.stringify(pathToFileURL(require.resolve('react')).href)+';\n'+ts.transpileModule(componentSource,{compilerOptions:options}).outputText.replace(/from ['"]\.\/safe-links['"]/u,'from '+JSON.stringify(helperUrl));
const {safeSourceLink}=await import(helperUrl),{SourceLink}=await import(moduleUrl(componentCode));
const report={tested_at:new Date().toISOString(),method:'Installed React/react-markdown server rendering with the actual SourceLink component and WHATWG URL normalization',browser_tested:false,network_requests:0,dns_resolution_performed:false,dns_rebinding_protection_claimed:false,cookie_port_isolation_claimed:false,cases:[],markdown_cases:[]};
const allowed=['https://docs.python.org/3.12/library/stdtypes.html#ranges','https://react.dev/learn','https://www.typescriptlang.org:443/docs/','https://مثال.إختبار/path','https://example.com./path'];
const blocked=['http://example.com/','http://127.0.0.1:8765/api/projects','https://127.0.0.1/api','https://127.1/api','https://2130706433/api','https://0x7f000001/api','https://0177.0.0.1/api','https://[::1]/api','https://[::ffff:127.0.0.1]/api','https://localhost/api','https://localhost./api','https://app.localhost/api','https://app.local/','https://app.internal/','https://intranet/','https://user:password@example.com/','https://example.com:8443/','//example.com/','/api/projects','#untrusted-fragment','javascript:alert(1)','data:text/html,test','mailto:user@example.com','https://%6cocalhost/','https://example.com\\@localhost/','https://example.com\n.evil.test/','https://127。0。0。1/','https://foo..example.com/'];
const reportUrl=new URL('../../reports/guard-links.json',import.meta.url);
await mkdir(new URL('../../reports/',import.meta.url),{recursive:true});
await copyFile(reportUrl,new URL('../../reports/guard-links-prior-'+Date.now()+'.json',import.meta.url)).catch(error=>{if(error.code!=='ENOENT')throw error;});
try{
 for(const [accept,values] of [[true,allowed],[false,blocked]])for(const url of values){
  const value=safeSourceLink(url),markup=renderToStaticMarkup(React.createElement(SourceLink,{href:url},'Visible source label'));
  assert.equal(Boolean(value),accept,url);assert.equal(markup.includes('<a '),accept,url);assert(markup.includes('Visible source label'));
  if(accept){assert(markup.includes('target="_blank"'));assert(markup.includes('rel="noopener noreferrer"'));}else assert(!markup.includes('href='));
  report.cases.push({input:url,allowed:accept,label_preserved:true});
 }
 const fixtures=[
  {name:'loopback link is inert',text:'[Keep this label](http://127.0.0.1:9999/api/steal)',anchors:0,label:'Keep this label'},
  {name:'numeric IPv4 normalization is inert',text:'[Numeric host](https://2130706433/api)',anchors:0,label:'Numeric host'},
  {name:'official HTTPS remains usable',text:'[Python documentation](https://docs.python.org/3.12/)',anchors:1,label:'Python documentation'},
  {name:'script scheme is inert',text:'[Unsafe link](javascript:alert%281%29)',anchors:0,label:'Unsafe link'},
  {name:'raw HTML SVG stays text',text:'<svg onload="alert(1)"><script>alert(1)</script></svg>',anchors:0,label:'&lt;svg'},
  {name:'remote image is inert',text:'![Image label](https://example.com/pixel.png)',anchors:0,label:'Image label'},
 ];
 for(const fixture of fixtures){
  const markup=renderToStaticMarkup(React.createElement(Markdown,{components:{a:({href,children})=>React.createElement(SourceLink,{href},children),img:({alt})=>React.createElement('span',null,'['+(alt||'image')+']')}},fixture.text));
  assert.equal((markup.match(/<a\s/gu)||[]).length,fixture.anchors,fixture.name);assert(markup.includes(fixture.label));assert(!/<(?:script|svg|img)\b/iu.test(markup));
  report.markdown_cases.push({name:fixture.name,passed:true});
 }
 report.status='passed';
}catch(error){report.status='failed';report.error=String(error.stack??error);process.exitCode=1;}
finally{await writeFile(reportUrl,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,url_cases:report.cases.length,markdown_cases:report.markdown_cases.length,report:'reports/guard-links.json'}));}
