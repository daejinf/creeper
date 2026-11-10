"""Local developer status, separate from the framework-free core revisions."""
import hashlib
import os
from pathlib import Path
from wemeet_ai import __version__
from wemeet_ai.m0.versions import ROOT, code_status

HERE=Path(__file__).resolve().parent


def digest(paths):
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted(paths))).hexdigest()[:16]


ADAPTER_REVISION=digest(HERE.glob('*.py'))


def status():
    core=code_status()
    return {**core,'version':__version__,'process_id':os.getpid(),
        'reload_mode':'AUTO' if os.getenv('WEMEET_DEV_RELOAD')=='1' else 'MANUAL',
        'adapter_reload_required':digest(HERE.glob('*.py'))!=ADAPTER_REVISION,
        'ui_revision':digest([p for p in HERE.iterdir() if p.suffix in {'.html','.js','.css'}]),
        'source_kind':'WORKSPACE' if ROOT==(HERE.parents[1]/'src'/'wemeet_ai') else 'INSTALLED_PACKAGE'}
