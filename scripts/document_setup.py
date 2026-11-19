"""Bounded document dependencies: local bundle reuse plus three pinned OCR data files."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path("D:/RiwaqRuntime/documents-v1")
BUNDLE=Path("C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1110-778-0_26.1110.778.0_x64__3k8sg7r9htsxt/dependencies")
REPORT=ROOT/"reports/document-runtime.json"
PHASE_CAP=200*1024**2
PRIOR=6455051898
AGGREGATE_CAP=8*1024**3
PYTHON_COMPONENTS={
 "pypdfium2":("5.14.0",["pypdfium2","pypdfium2_raw","pypdfium2_cfg","pypdfium2_cli","pypdfium2-5.14.0.dist-info"]),
 "openpyxl":("3.1.5",["openpyxl","openpyxl-3.1.5.dist-info"]),
 "et-xmlfile":("2.0.0",["et_xmlfile","et_xmlfile-2.0.0.dist-info"]),
 "python-pptx":("1.0.2",["pptx","python_pptx-1.0.2.dist-info"]),
 "lxml":("6.1.1",["lxml","lxml-6.1.1.dist-info"]),
 "XlsxWriter":("3.2.9",["xlsxwriter","xlsxwriter-3.2.9.dist-info"]),
}
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for block in iter(lambda:f.read(1024**2),b""):h.update(block)
 return h.hexdigest()
def write(p,data):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 temp=p.with_suffix(p.suffix+".tmp")
 temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
 temp.replace(p)
def book():
 if REPORT.exists():
  data=json.loads(REPORT.read_text(encoding="utf-8"))
  if data["phase_cap_bytes"]!=PHASE_CAP or data["prior_aggregate_bytes"]!=PRIOR:raise ValueError("Existing ledger bounds differ")
  return data
 return {"version":"document-runtime-v1","phase_cap_bytes":PHASE_CAP,"prior_aggregate_bytes":PRIOR,
         "aggregate_cap_bytes":AGGREGATE_CAP,"received_bytes":0,"requests":[],
         "budget_scope":"HTTP response-body bytes including failed attempts; local copied bytes excluded",
         "paid_services_used":False,"systemwide_install":False,"training":False,"status":"preparing","runtime":str(RUNTIME)}
def save_book(data):
 data["aggregate_received_bytes"]=PRIOR+data["received_bytes"];write(REPORT,data)
def inside(root,rel):
 root=Path(root).resolve();p=(root/rel).resolve()
 if p==root or not p.is_relative_to(root):raise ValueError("Path outside runtime")
 return p
def fetch(url,path,limit,expected_size=None,git_blob_sha=None):
 path=Path(path);data=book()
 if path.exists():
  for r in reversed(data["requests"]):
   if r.get("path")==str(path) and r.get("status")=="complete" and sha(path)==r.get("sha256"):return path.read_bytes()
  raise ValueError("Unrecorded or changed download: "+str(path))
 remaining=min(PHASE_CAP-data["received_bytes"],AGGREGATE_CAP-PRIOR-data["received_bytes"])
 if limit>remaining:raise ValueError("Request exceeds remaining authorized download budget")
 if not(url.startswith("https://api.github.com/repos/tesseract-ocr/tessdata_fast/") or url.startswith("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/")):
  raise ValueError("Only approved Tesseract repository hosts/paths")
 row={"url":url,"path":str(path),"status":"started","bytes":0,"started_unix":time.time()}
 data["requests"].append(row);save_book(data);path.parent.mkdir(parents=True,exist_ok=True)
 partial=path.with_suffix(path.suffix+".part");digest=hashlib.sha256()
 try:
  request=urllib.request.Request(url,headers={"User-Agent":"Riwaq-document-setup/1","Accept":"application/vnd.github+json"})
  with urllib.request.urlopen(request,timeout=20) as response,partial.open("wb") as output:
   final=response.geturl()
   if not final.startswith(("https://api.github.com/","https://raw.githubusercontent.com/")):raise ValueError("Unapproved redirect")
   row["http_status"]=response.status
   declared=response.headers.get("Content-Length")
   if declared and int(declared)>limit:raise ValueError("Response exceeds planned bound")
   while True:
    allowance=min(64*1024,limit-row["bytes"])
    if allowance<=0:raise ValueError("Response reached bounded transfer limit")
    block=response.read(allowance)
    if not block:break
    output.write(block);digest.update(block);row["bytes"]+=len(block);data["received_bytes"]+=len(block)
    save_book(data)
  if expected_size is not None and row["bytes"]!=expected_size:raise ValueError("Downloaded size mismatch")
  value=partial.read_bytes()
  if git_blob_sha and hashlib.sha1(b"blob "+str(len(value)).encode()+b"\0"+value).hexdigest()!=git_blob_sha:
   raise ValueError("Pinned Git blob integrity mismatch")
  row.update(status="complete",sha256=digest.hexdigest());partial.replace(path);save_book(data)
  return value
 except BaseException as error:
  row.update(status="failed",error=type(error).__name__+": "+str(error)[:300],partial_sha256=digest.hexdigest());save_book(data);raise
def copy_tree(source,target,records):
 source=Path(source);target=Path(target)
 if not source.is_dir():raise ValueError("Bundled component missing: "+str(source))
 for src in sorted(source.rglob("*")):
  if "__pycache__" in src.parts or src.suffix in {".pyc",".pyo"} or not src.is_file():continue
  if src.is_symlink() or not src.resolve().is_relative_to(BUNDLE.resolve()):raise ValueError("Unexpected bundled link")
  dst=inside(target,src.relative_to(source));dst.parent.mkdir(parents=True,exist_ok=True)
  fingerprint=sha(src)
  if dst.exists():
   if sha(dst)!=fingerprint:raise ValueError("Existing runtime file differs: "+str(dst))
  else:shutil.copyfile(src,dst)
  records[str(dst.relative_to(RUNTIME)).replace("\\","/")]={"bytes":dst.stat().st_size,"sha256":fingerprint,"source":str(src)}
def reuse():
 RUNTIME.mkdir(parents=True,exist_ok=True);manifest_path=RUNTIME/"runtime-manifest.json"
 manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
  "version":"document-runtime-v1","bundle":str(BUNDLE),"files":{},"python_packages":{},"node_packages":{},"languages":{}}
 records=manifest["files"];site=BUNDLE/"python/Lib/site-packages"
 for name,(version,folders) in PYTHON_COMPONENTS.items():
  for folder in folders:copy_tree(site/folder,RUNTIME/"python-packages"/folder,records)
  manifest["python_packages"][name]={"version":version,"origin":"copied installed bundled distribution; not a rebuilt wheel"}
 module_root=BUNDLE/"node/node_modules";pending=["tesseract.js"];seen=set()
 while pending:
  name=pending.pop()
  if name in seen:continue
  if len(seen)>=64 or name.startswith((".","/")) or ".." in Path(name).parts:raise ValueError("Unexpected dependency graph")
  package=inside(module_root,name);metadata=json.loads((package/"package.json").read_text(encoding="utf-8"))
  if name in {"tesseract.js","tesseract.js-core"} and metadata["version"]!="7.0.0":raise ValueError("OCR version changed")
  copy_tree(package,RUNTIME/"node_modules"/name,records);seen.add(name)
  manifest["node_packages"][name]={"version":metadata["version"],"license":metadata.get("license"),"source":str(package)}
  pending.extend(metadata.get("dependencies",{}))
 manifest["paths"]={"python_packages":str(RUNTIME/"python-packages"),"node_modules":str(RUNTIME/"node_modules"),
                    "tesseract_package":str(RUNTIME/"node_modules/tesseract.js"),
                    "tesseract_worker":str(RUNTIME/"node_modules/tesseract.js/src/worker-script/node/index.js"),
                    "tesseract_core":str(RUNTIME/"node_modules/tesseract.js-core"),
                    "tessdata":str(RUNTIME/"tessdata")}
 manifest["pdfium"]={"python_version":"5.14.0",**json.loads((site/"pypdfium2_raw/version.json").read_text())}
 manifest["local_copied_bytes"]=sum(x["bytes"] for x in records.values() if "source" in x)
 write(manifest_path,manifest)
 data=book();data.update(status="local_dependencies_copied",manifest=str(manifest_path),manifest_sha256=sha(manifest_path),python_packages=manifest["python_packages"],node_packages=manifest["node_packages"],paths=manifest["paths"],local_copied_bytes=manifest["local_copied_bytes"]);save_book(data)
 print(json.dumps({"stage":"reuse_complete","files":len(records),"copied_bytes":manifest["local_copied_bytes"]}),flush=True)
def languages():
 meta=RUNTIME/"metadata";pin=meta/"tessdata-commit.json"
 commit_data=json.loads(fetch("https://api.github.com/repos/tesseract-ocr/tessdata_fast/commits/main",pin,1024**2))
 commit=commit_data["sha"]
 if len(commit)!=40 or any(c not in "0123456789abcdef" for c in commit):raise ValueError("Invalid repository commit")
 manifest_path=RUNTIME/"runtime-manifest.json";manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
 tree_sha=commit_data["commit"]["tree"]["sha"]
 tree=json.loads(fetch(f"https://api.github.com/repos/tesseract-ocr/tessdata_fast/git/trees/{tree_sha}",meta/"tessdata-tree.json",2*1024**2))
 if tree.get("truncated"):raise ValueError("Incomplete repository tree")
 entries={x["path"]:x for x in tree["tree"]}
 for lang in ["ara","eng","heb"]:
  name=lang+".traineddata"
  entry=entries[name]
  if entry["type"]!="blob" or entry["size"]>32*1024**2 or entry["path"]!=name:raise ValueError("Unexpected OCR model size/name")
  path=RUNTIME/"tessdata"/name
  fetch(f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{commit}/{name}",path,entry["size"]+1,entry["size"],entry["sha"])
  record={"bytes":path.stat().st_size,"sha256":sha(path),"git_blob_sha1":entry["sha"],"revision":commit,"license":"Apache-2.0"}
  manifest["languages"][lang]=record;manifest["files"]["tessdata/"+name]=record
 license_path=RUNTIME/"licenses/tessdata_fast-LICENSE"
 fetch(f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{commit}/LICENSE",license_path,100000,entries["LICENSE"]["size"],entries["LICENSE"]["sha"])
 if "Apache License" not in license_path.read_text():raise ValueError("Unexpected OCR data license")
 manifest["files"]["licenses/tessdata_fast-LICENSE"]={"bytes":license_path.stat().st_size,"sha256":sha(license_path)}
 manifest["tessdata_revision"]=commit;write(manifest_path,manifest)
 data=book();data.update(status="dependencies_downloaded_not_ocr_quality_tested",languages=manifest["languages"],manifest_sha256=sha(manifest_path));save_book(data)
 print(json.dumps({"stage":"languages_verified","revision":commit,"received_bytes":data["received_bytes"]}),flush=True)
def verify():
 p=RUNTIME/"runtime-manifest.json";manifest=json.loads(p.read_text(encoding="utf-8"))
 for name,record in manifest["files"].items():
  f=inside(RUNTIME,name)
  if f.stat().st_size!=record["bytes"] or sha(f)!=record["sha256"]:raise ValueError("Runtime file integrity failure: "+name)
 probe="import sys,io,json;sys.path.insert(0,"+repr(str(RUNTIME/"python-packages"))+");import pypdfium2 as pdfium,openpyxl,pptx;d=pdfium.PdfDocument.new();p=d.new_page(72,72);b=p.render(scale=1);size=b.to_pil().size;b.close();p.close();d.close();w=openpyxl.Workbook();w.active['A1']='local';f=io.BytesIO();w.save(f);f.seek(0);r=openpyxl.load_workbook(f,read_only=True);assert r.active['A1'].value=='local';r.close();s=pptx.Presentation();s.slides.add_slide(s.slide_layouts[6]);print(json.dumps({'pdfium_render':list(size),'openpyxl_roundtrip':True,'pptx_empty_slide':len(s.slides)}))"
 env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"}
 output=subprocess.check_output([sys.executable,"-B","-c",probe],env=env,text=True,timeout=30)
 data=book();data.update(status="python_dependencies_smoke_verified",python_smoke=json.loads(output),manifest_sha256=sha(p),integrity_file_count=len(manifest["files"]),ocr_quality_tested=False);save_book(data)
 print(output.strip(),flush=True)
if __name__=="__main__":
 parser=argparse.ArgumentParser();parser.add_argument("action",choices=["reuse","languages","verify","all"]);a=parser.parse_args()
 save_book(book())
 if a.action in {"reuse","all"}:reuse()
 if a.action in {"languages","all"}:languages()
 if a.action in {"verify","all"}:verify()
