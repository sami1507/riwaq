"""Write the fixed worker build declaration, not runtime acceptance."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.packaged_workers import VERSION,KINDS
folder=Path(sys.argv[1]);folder.mkdir(parents=True,exist_ok=True)
files=['backend/packaged_workers.py','backend/check_worker.py','backend/coding_context.py','backend/isolated_worker.py','backend/isolated_bootstrap.py','backend/reasoning_math.py','backend/launcher.py','backend/isolated_audio.py','backend/audio_isolation.py','backend/audio_decode.py','scripts/isolated_audio_worker.py']
files+=['backend/voice_audio.py','backend/voice_worker.py']
value={'version':VERSION,'dispatch':'fixed-v1','workers':sorted(KINDS),'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files},'tested':False,'external_requirements':['pinned Docker client/daemon/images for container workers']}
(folder/'packaged-workers.json').write_text(json.dumps(value,sort_keys=True,indent=2),encoding='utf-8')
