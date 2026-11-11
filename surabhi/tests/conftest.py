"""Dashboard tests: import the dashboard modules (storage, ui, ...) and the shared schemas.py at the repo root,
and keep the FORGE engine's cache / output in throw-away folders."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent))
_tmp = tempfile.mkdtemp(prefix='forge-dashboard-tests-')
os.environ.setdefault('FORGE_CACHE_DIR', str(Path(_tmp) / 'cache'))
os.environ.setdefault('FORGE_OUTPUT_DIR', str(Path(_tmp) / 'output'))
os.environ.pop('FORGE_STATE_FILE', None)
