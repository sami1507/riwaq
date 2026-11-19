import os
import sys
from pathlib import Path

def main():
    if len(sys.argv)>=3 and sys.argv[1]=='--fixed-worker':
        from backend.packaged_workers import dispatch
        return dispatch(sys.argv[2],sys.argv[3:]) if len(sys.argv)>3 else dispatch(sys.argv[2])
    # Fixed workers intentionally receive no user-profile environment and must
    # never initialize the service's persistent data directory.
    if getattr(sys, 'frozen', False):
        if 'RIWAQ_DATA_DIR' not in os.environ:
            local = os.environ.get('LOCALAPPDATA')
            os.environ['RIWAQ_DATA_DIR'] = str((Path(local) if local else Path.home()) / 'Riwaq')
        os.environ.setdefault('RIWAQ_STATIC_DIR', str(Path(sys._MEIPASS) / 'frontend' / 'dist'))
    if sys.argv[1:]:
        if sys.argv[1:] == ['--pair-browser', '--show']:
            from backend.local_auth import main as pair
            return pair(['pair', '--show'])
        raise SystemExit('Unsupported launcher arguments. Explicit local pairing: --pair-browser --show')
    port=int(os.environ.get('RIWAQ_SERVICE_PORT','8765'))
    if not 1024<=port<=65535:raise SystemExit('Invalid local service port')
    import uvicorn
    uvicorn.run('backend.app:app',host='127.0.0.1',port=port,log_level='info',access_log=False,proxy_headers=False)

if __name__ == '__main__':
    main()
