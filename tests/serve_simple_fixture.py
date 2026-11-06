"""无AI Key的隔离简易版验收入口，不访问用户项目。"""
import os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['WQ_PROJECT_HOME']=tempfile.mkdtemp(prefix='simple-mode-')
from backend.web import create_app
from backend.routers import system
import uvicorn
system.resolve_api_key=lambda name:''
if __name__=='__main__':uvicorn.run(create_app(),host='127.0.0.1',port=5184)
