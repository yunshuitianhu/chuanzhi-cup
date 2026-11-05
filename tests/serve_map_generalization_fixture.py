"""Isolated local production UI fixture; requires generated synthetic test packages."""
from pathlib import Path
import json
import os
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['WQ_PROJECT_HOME']=tempfile.mkdtemp(prefix='map-generalization-')
from backend.web import create_app
from backend.project_codec import load_project
from backend.session import STORE
import uvicorn

if __name__=='__main__':
    records=[]
    for path in sorted(Path('output/map-generalization-data').glob('*.wqproj')):
        snapshot=load_project(path.read_bytes());session=STORE.create()
        for key,value in snapshot.state.items():setattr(session,key,value)
        session.river_ui.pop('playback_reference',None)
        records.append(dict(sample=path.stem,sid=session.id))
    if len(records)!=6:raise RuntimeError('Run test_playback_reference_generalization.py first')
    Path('output/map-generalization-ui-sessions.json').write_text(json.dumps(records),encoding='utf-8')
    uvicorn.run(create_app(),host='127.0.0.1',port=5184)
