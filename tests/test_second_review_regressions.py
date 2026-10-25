"""二次复审：空库就绪、冷启动并发、索引选择和上传读取上限。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier,BrokenBarrierError
from types import ModuleType,SimpleNamespace
import sys,tempfile,unittest
from unittest.mock import patch
from fastapi import HTTPException
from backend.routers import data
from backend.session import Session
from water_quality_mcp.src.water_quality_mcp import rag_engine as core

class RagConcurrencyReviewTests(unittest.TestCase):
    def test_empty_index_does_not_validate_embedding_retrieval(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=core.RAGEngine(folder);engine._collection=SimpleNamespace(count=lambda:0)
            self.assertEqual(engine.retrieve('受控查询'),[])
            self.assertFalse(engine._retrieval_verified)
            self.assertIsNone(engine._embedding_model)

    def test_cold_parallel_model_access_loads_once(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=core.RAGEngine(folder);gate=Barrier(2);calls=[]
            module=ModuleType('sentence_transformers')
            def create(*args,**kwargs):
                calls.append(1)
                try:gate.wait(timeout=.2)
                except BrokenBarrierError:pass
                return SimpleNamespace(get_embedding_dimension=lambda:768)
            module.SentenceTransformer=create
            with patch.dict(sys.modules,{'sentence_transformers':module}),ThreadPoolExecutor(2) as executor:
                models=list(executor.map(lambda _:engine.embedding_model,range(2)))
            self.assertEqual(len(calls),1);self.assertIs(models[0],models[1])

    def test_cold_parallel_singleton_initialization_loads_once(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'configured_index_dir',return_value=folder):
            gate=Barrier(2);calls=[]
            def create(path=None):
                calls.append(1)
                try:gate.wait(timeout=.2)
                except BrokenBarrierError:pass
                return SimpleNamespace(_persist_dir=str(Path(path or folder).resolve()))
            with patch.object(core,'RAGEngine') as cls:
                cls._instance=None;cls.side_effect=create
                with ThreadPoolExecutor(2) as executor:engines=list(executor.map(lambda _:core.get_rag_engine(),range(2)))
            self.assertEqual(len(calls),1);self.assertIs(engines[0],engines[1])

    def test_explicit_index_argument_uses_requested_index_without_switching_default(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b,patch.object(core,'configured_index_dir',return_value=a),patch.object(core.RAGEngine,'_instance',None):
            first=core.get_rag_engine();other=core.get_rag_engine(b)
            self.assertEqual(Path(other._persist_dir),Path(b));self.assertIsNot(first,other)
            self.assertIs(core.get_rag_engine(),first)

class BoundedUploadReviewTests(unittest.IsolatedAsyncioTestCase):
    async def check_upload(self,fn,key):
        class OversizedFile:
            filename='controlled.bin'
            def __init__(self):self.read_size=None
            async def read(self,size=-1):
                self.read_size=size
                if size<0:raise AssertionError('上传不应先无上限读入整个文件')
                return b'x'*size
        file=OversizedFile();session=Session('bounded-controlled')
        with patch.object(data,key,1):
            with self.assertRaises(HTTPException) as caught:
                if fn is data.upload_video:await fn(file=file,interval=5,session=session)
                else:await fn(file=file,session=session)
        self.assertEqual(caught.exception.status_code,413)
        self.assertEqual(file.read_size,1024*1024+1)
        self.assertIsNone(session.df);self.assertEqual(session.video_frames,[])
    async def test_excel_upload_rejects_oversize_without_unbounded_read(self):await self.check_upload(data.upload_data,'MAX_DATA_MB')
    async def test_video_upload_rejects_oversize_without_unbounded_read(self):await self.check_upload(data.upload_video,'MAX_VIDEO_MB')
