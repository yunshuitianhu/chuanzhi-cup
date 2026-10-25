"""知识库状态与配置不能把“索引存在”冒充“真实检索已成功”。"""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace,ModuleType
import sys
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from backend.routers import rag
from water_quality_mcp.src.water_quality_mcp import rag_engine as core

class RagReadinessTests(unittest.TestCase):
    def status(self,engine,dependencies=None):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'chroma.sqlite3').write_bytes(b'controlled-index')
            with patch.object(rag,'INDEX_DIR',folder),patch.object(rag,'get_rag_engine',return_value=engine),patch.object(core,'optional_dependencies',return_value=dependencies or {'chromadb':True,'sentence_transformers':True},create=True):
                return rag.status()

    def engine(self,verified=False):
        collection=SimpleNamespace(count=lambda:147)
        return SimpleNamespace(collection=collection,_retrieval_verified=verified,_retrieval_error=None,
            _chroma_client=object(),_collection=collection)

    def test_index_count_does_not_claim_embedding_retrieval_ready(self):
        result=self.status(self.engine())
        self.assertTrue(result['available']);self.assertEqual(result['state'],'index_ready')
        self.assertFalse(result['retrieval_verified'])

    def test_missing_model_dependency_is_not_hidden_by_readable_index(self):
        result=self.status(self.engine(),{'chromadb':True,'sentence_transformers':False})
        self.assertFalse(result['available']);self.assertEqual(result['state'],'dependency_missing')
        self.assertIn('sentence_transformers',result['missing_dependencies'])

    def test_successful_real_retrieval_is_distinct_from_index_only(self):
        result=self.status(self.engine(True));self.assertEqual(result['state'],'ready')
        self.assertTrue(result['retrieval_verified'])

    def test_status_does_not_reset_shared_chroma_client(self):
        engine=self.engine(True);client=engine._chroma_client;collection=engine._collection
        self.status(engine)
        self.assertIs(engine._chroma_client,client);self.assertIs(engine._collection,collection)

    def test_index_read_failure_is_not_reported_as_missing_index(self):
        engine=self.engine();engine.collection=MagicMock();engine.collection.count.side_effect=OSError('controlled read failure')
        result=self.status(engine)
        self.assertEqual(result['state'],'index_unreadable');self.assertFalse(result['available'])

    def test_configured_index_and_model_do_not_depend_on_user_desktop_location(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'WQ_RAG_INDEX_DIR':folder,'WQ_EMBEDDING_MODEL':'controlled-local-model'}):
            engine=core.RAGEngine();self.assertEqual(Path(engine._persist_dir),Path(folder))
            module=ModuleType('sentence_transformers');module.SentenceTransformer=MagicMock()
            with patch.dict(sys.modules,{'sentence_transformers':module}):
                _=engine.embedding_model
            self.assertEqual(module.SentenceTransformer.call_args.args[0],'controlled-local-model')
            self.assertFalse(module.SentenceTransformer.call_args.kwargs['trust_remote_code'])

    def test_real_retrieve_success_and_failure_update_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=core.RAGEngine(folder)
            engine._collection=MagicMock()
            engine._collection.count.return_value=1
            engine._collection.query.return_value={'ids':[[]]}
            engine._embedding_model=MagicMock()
            engine._embedding_model.encode.return_value=SimpleNamespace(tolist=lambda:[0.,1.])
            self.assertEqual(engine.retrieve('受控检索',strategy='dense_v1'),[])
            engine._embedding_model.encode.assert_called_once()
            self.assertTrue(engine._retrieval_verified)
            engine._collection.query.side_effect=RuntimeError('controlled')
            with self.assertRaises(RuntimeError):engine.retrieve('受控检索',strategy='dense_v1')
            self.assertFalse(engine._retrieval_verified);self.assertEqual(engine._retrieval_error,'RuntimeError')
