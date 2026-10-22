"""RAG 知识库路由：状态查询与语义检索。

知识库为本地整理的治理参考资料（Markdown + YAML frontmatter），
经 text2vec-base-chinese 嵌入后存入 ChromaDB（`.rag_index/`）。
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from water_quality_mcp.src.water_quality_mcp.rag_engine import (
    COLLECTION_NAME, EMBEDDING_MODEL_NAME, get_rag_engine, search_treatment_cases,
)

from .. import PROJECT_ROOT
from ..deps import get_session
from ..session import Session

router = APIRouter(prefix="/rag", tags=["rag"])

INDEX_DIR = os.path.join(PROJECT_ROOT, ".rag_index")
KB_DIR = os.path.join(PROJECT_ROOT, "knowledge_base")


@router.get("/status")
def status(session: Session = Depends(get_session)) -> dict:
    """知识库可用性与规模。

    只做轻量探测：先看索引文件在不在，再尝试取集合计数。
    ChromaDB 连接失败不算错误（Windows 上偶发文件锁），如实返回即可。
    """
    sqlite_path = os.path.join(INDEX_DIR, "chroma.sqlite3")
    has_index = os.path.isfile(sqlite_path) and os.path.getsize(sqlite_path) > 0

    doc_count = 0
    if os.path.isdir(KB_DIR):
        for root, _, files in os.walk(KB_DIR):
            doc_count += sum(
                1 for f in files
                if f.endswith(".md") and not f.startswith("_") and f != "README.md"
            )

    chunks = None
    error = None
    if has_index:
        try:
            engine = get_rag_engine()
            engine._collection = None
            engine._chroma_client = None
            chunks = engine.collection.count()
        except Exception as e:                       # noqa: BLE001
            error = f"{type(e).__name__}: {e}"

    return {
        "available": bool(has_index and chunks),
        "has_index": has_index,
        "chunks": chunks,
        "source_documents": doc_count,
        "collection": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL_NAME,
        "index_dir": INDEX_DIR,
        "error": error,
    }


class SearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query: str = Field(min_length=1,max_length=1000)
    top_k: int = Field(default=5,ge=1,le=10)


@router.post("/search")
def search(payload: SearchRequest, session: Session = Depends(get_session)) -> dict:
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

    return data
