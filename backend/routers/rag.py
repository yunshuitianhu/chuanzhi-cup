"""RAG 知识库路由：状态查询与语义检索。

知识库为本地整理的治理参考资料（Markdown + YAML frontmatter），
经 text2vec-base-chinese 嵌入后存入 ChromaDB（`.rag_index/`）。
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from water_quality_mcp.src.water_quality_mcp import rag_engine as core
from water_quality_mcp.src.water_quality_mcp.rag_engine import (
    COLLECTION_NAME, EMBEDDING_MODEL_NAME, get_rag_engine, search_treatment_cases,
)

from .. import PROJECT_ROOT
from ..deps import get_session, get_exclusive_session
from ..question_context import register_retrieval
from ..session import Session

router = APIRouter(prefix="/rag", tags=["rag"])

INDEX_DIR = core.configured_index_dir()
KB_DIR = os.path.join(PROJECT_ROOT, "knowledge_base")


@router.get("/status")
def status(session: Session = Depends(get_session)) -> dict:
    """知识库可用性与规模。

    只做轻量探测：先看索引文件在不在，再尝试取集合计数。
    ChromaDB 连接失败不算错误（Windows 上偶发文件锁），如实返回即可。
    """
    sqlite_path = os.path.join(INDEX_DIR, "chroma.sqlite3")
    dependencies = core.optional_dependencies()
    missing = [name for name, found in dependencies.items() if not found]
    has_index = False
    chunks = None
    error = None
    engine = None
    try:
        has_index = os.path.isfile(sqlite_path) and os.path.getsize(sqlite_path) > 0
        if has_index and dependencies['chromadb']:
            engine = get_rag_engine()
            # 状态查询不能重置正在被报告/检索共用的客户端和集合。
            chunks = engine.collection.count()
    except Exception as exc:
        error = f'索引无法读取（{type(exc).__name__}），请核对目录权限与依赖版本。'

    doc_count = 0
    if os.path.isdir(KB_DIR):
        for _, _, files in os.walk(KB_DIR):
            doc_count += sum(1 for name in files if name.endswith('.md')
                and not name.startswith('_') and name != 'README.md')
    index_ready = bool(has_index and chunks and not error)
    verified = bool(index_ready and not missing and getattr(engine, '_retrieval_verified', False))
    last_error = getattr(engine, '_retrieval_error', None)
    if missing:
        state = 'dependency_missing'
        notice = '知识库依赖缺失：' + '、'.join(missing) + '；请使用安装了完整项目依赖的Python启动。'
    elif error:
        state, notice = 'index_unreadable', error
    elif not has_index:
        state, notice = 'index_missing', '当前索引目录没有向量库；请核对配置目录或构建索引。'
    elif not chunks:
        state, notice = 'index_empty', '当前索引可读取但没有资料块，请构建知识库索引。'
    elif last_error:
        state, notice = 'retrieval_failed', f'索引可读，但最近检索失败（{last_error}）；请核查模型与依赖后重试。'
    elif verified:
        state, notice = 'ready', '当前进程已完成实际检索；资料片段仍需核对，不代表建议或现场结论正确。'
    else:
        state, notice = 'index_ready', '索引可读；嵌入模型按需加载，尚未验证实际检索。'
    stages = {'dependency_missing': 'dependencies', 'index_unreadable': 'index_read',
              'index_missing': 'index_build', 'index_empty': 'index_build',
              'retrieval_failed': 'retrieval', 'index_ready': 'model_load', 'ready': 'retrieval'}
    return {'available': bool(index_ready and not missing), 'index_ready': index_ready,
        'stage': stages.get(state, 'unknown'),
        'retrieval_verified': verified, 'state': state, 'notice': notice,
        'missing_dependencies': missing, 'has_index': has_index, 'chunks': chunks,
        'source_documents': doc_count, 'collection': COLLECTION_NAME,
        'embedding_model': core.configured_embedding_model(), 'index_dir': INDEX_DIR, 'error': error}



class SearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query: str = Field(min_length=1,max_length=1000)
    top_k: int = Field(default=5,ge=1,le=10)


@router.post("/search")
def search(payload: SearchRequest, session: Session = Depends(get_exclusive_session)) -> dict:
    """语义检索治理案例。

    Body: { query: str, top_k?: int }
    """
    query = payload.query.strip()
    if not query:
        raise HTTPException(400, "查询不能为空")
    top_k = payload.top_k

    raw = search_treatment_cases(query, top_k)
    data = json.loads(raw) if isinstance(raw, str) else raw

    if "检索结果" not in data:
        # 内核在索引缺失/依赖问题时返回 {"状态": ..., "建议": ...}
        raise HTTPException(503, data.get("状态", "知识库不可用"))

    return {**data,'retrieval_id':register_retrieval(session,data)}
