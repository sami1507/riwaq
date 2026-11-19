"""Source launcher for the same fixed voice worker used by packaged dispatch."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.voice_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
