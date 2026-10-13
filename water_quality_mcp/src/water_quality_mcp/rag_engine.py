"""RAG 核心引擎 — 嵌入模型管理、ChromaDB 检索、结果格式化。

用法：
    from water_quality_mcp.src.water_quality_mcp.rag_engine import (
        get_rag_engine,
        search_treatment_cases,
    )

    engine = get_rag_engine()                    # 单例，首次加载嵌入模型
    results = engine.retrieve("氨氮超标 黑臭水体", top_k=5)
    context = engine.format_context(results)      # 格式化为 prompt 文本
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger(__name__)

# 默认 ChromaDB 持久化目录（相对于项目根目录）
DEFAULT_PERSIST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".rag_index"
)

# 嵌入模型名称（中文语义）
EMBEDDING_MODEL_NAME = "shibing624/text2vec-base-chinese"

# 集合名称
COLLECTION_NAME = "treatment_cases"


class RAGEngine:
    """RAG 检索引擎（单例模式）。

    管理 sentence-transformers 嵌入模型和 ChromaDB 向量存储。
    首次实例化时自动下载嵌入模型（~400MB），后续调用复用已加载的模型。
    """

    _instance: RAGEngine | None = None

    def __init__(self, persist_dir: str | None = None):
        """初始化 RAG 引擎。

        Args:
            persist_dir: ChromaDB 持久化目录。默认使用项目根目录下的 .rag_index。
        """
        self._persist_dir = os.path.abspath(
            persist_dir or DEFAULT_PERSIST_DIR
        )
        os.makedirs(self._persist_dir, exist_ok=True)

        self._embedding_model = None
        self._chroma_client: chromadb.PersistentClient | None = None
        self._collection: Any = None

    # ---- 嵌入模型 ----

    @property
    def embedding_model(self):
        """延迟加载 sentence-transformers 嵌入模型（单例）。"""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("加载嵌入模型: %s", EMBEDDING_MODEL_NAME)
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info(
                "嵌入模型已加载，向量维度: %d",
                self._embedding_model.get_embedding_dimension(),
            )
        return self._embedding_model

    # ---- ChromaDB ----

    @property
    def chroma_client(self) -> chromadb.PersistentClient:
        """延迟初始化 ChromaDB 持久化客户端。"""
        if self._chroma_client is None:
            self._chroma_client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._chroma_client

    @property
    def collection(self):
        """获取或创建 ChromaDB 集合。"""
        if self._collection is None:
            self._collection = self.chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # ---- 检索 ----

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """语义检索相似治理案例。

        Args:
            query: 查询字符串（自然语言描述）。
            top_k: 返回结果数。
            filters: ChromaDB where 过滤条件，可按元数据字段过滤。
                例如：{"pollutant_types": {"$contains": "氨氮"}}

        Returns:
            检索结果列表，每项包含 id, document, metadata, distance。
            按相似度降序排列。
        """
        collection = self.collection
        if collection.count() == 0:
            logger.warning("知识库为空，返回空结果")
            return []

        query_embedding = self.embedding_model.encode(
            query, normalize_embeddings=True
        ).tolist()

        where = filters or {}
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count()),
            where=where if where else None,
        )

        # ChromaDB 返回格式：{ids: [[...]], documents: [[...]], metadatas: [[...]], distances: [[...]]}
        hits = []
        if results and results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                hits.append({
                    "id": doc_id,
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    "distance": results["distances"][0][i] if results.get("distances") else 0.0,
                })
        return hits

    # ---- 格式化 ----

    def format_context(self, results: list[dict[str, Any]]) -> str:
        """将检索结果格式化为 LLM prompt 可用的参考案例文本。

        Args:
            results: retrieve() 返回的结果列表。

        Returns:
            格式化的 markdown 文本，可直接注入到 prompt 中。
        """
        if not results:
            return "（知识库中暂无相关治理案例）"

        lines = []
        for i, hit in enumerate(results, 1):
            meta = hit.get("metadata", {})
            title = meta.get("title", "未知案例")
            location = meta.get("location", "未知")
            severity = meta.get("severity", "未知")
            methods = meta.get("treatment_methods", "未知")
            doc = hit.get("document", "")

            # 截断过长文本，保留核心内容
            if len(doc) > 800:
                doc = doc[:800] + "\n...(内容已截断)"

            lines.append(
                f"### 案例 {i}：{title}\n"
                f"- **地点**：{location}\n"
                f"- **污染程度**：{severity}\n"
                f"- **治理方法**：{methods}\n"
                f"- **相似度**：{1 - hit['distance']:.2f}\n"
                f"\n{doc}\n"
            )

        return "\n".join(lines)

    # ---- 查询构造 ----

    def build_query_from_summary(self, summary: str) -> str:
        """从 comprehensive_summary JSON 中提取检索查询。

        解析 summary 中的关键字段（污染物类型、黑臭等级、位置等），
        构造自然语言查询字符串用于语义检索。

        Args:
            summary: comprehensive_summary 输出的 JSON 字符串或纯文本。

        Returns:
            适合语义检索的查询字符串。
        """
        parts = []

        # 尝试解析 JSON
        try:
            data = json.loads(summary) if isinstance(summary, str) else summary
        except (json.JSONDecodeError, TypeError):
            data = None

        if isinstance(data, dict):
            # 从黑臭检测中提取关键信息
            black_odors = data.get("黑臭检测", {})
            if isinstance(black_odors, dict):
                black_count = black_odors.get("黑臭点总数", 0)
                if black_count:
                    parts.append(f"黑臭水体 {black_count}个黑臭点")

                top5 = black_odors.get("Top5最严重点位", [])
                if top5 and isinstance(top5, list):
                    # 提取主要超标指标
                    pollutants = set()
                    for pt in top5[:3]:
                        if isinstance(pt, dict):
                            nh3 = pt.get("氨氮(mg/L)")
                            do_val = pt.get("溶解氧(mg/L)")
                            if nh3 is not None and isinstance(nh3, (int, float)) and nh3 > 2:
                                pollutants.add("氨氮超标")
                            if do_val is not None and isinstance(do_val, (int, float)) and do_val < 2:
                                pollutants.add("溶解氧低")
                    if pollutants:
                        parts.extend(pollutants)

                severity = black_odors.get("黑臭等级分布", {})
                if isinstance(severity, dict):
                    if severity.get("重度黑臭", 0) > 0:
                        parts.append("重度黑臭")
                    elif severity.get("轻度黑臭", 0) > 0:
                        parts.append("轻度黑臭")

                # 提取聚类中的典型污染物
                clusters = black_odors.get("聚类详情", [])
                if clusters and isinstance(clusters, list):
                    all_pollutants = set()
                    for c in clusters[:3]:
                        if isinstance(c, dict):
                            trigger = c.get("主要触发指标", "")
                            if trigger:
                                all_pollutants.add(trigger)
                    if all_pollutants:
                        parts.append("主要污染物: " + "、".join(all_pollutants))

            # 从空间分析中提取位置
            spatial = data.get("空间分析", {})
            if isinstance(spatial, dict):
                center = spatial.get("最密集聚类中心", {})
                if isinstance(center, dict):
                    loc = center.get("中心坐标", "")
                    if loc:
                        # 经纬度不太适合直接查询，跳过
                        pass

            # 从河道模型中提取
            river = data.get("河道模型", {})
            if isinstance(river, dict):
                if river.get("已初始化"):
                    wq = river.get("水质参数", {})
                    if isinstance(wq, dict):
                        pollutant = wq.get("污染物类型", "")
                        if pollutant:
                            parts.append(f"{pollutant}污染扩散")
        else:
            # 纯文本 summary，用关键词匹配
            text = str(summary)
            if "氨氮" in text:
                parts.append("氨氮超标")
            if "黑臭" in text:
                parts.append("黑臭水体")
            if "COD" in text or "cod" in text.lower():
                parts.append("COD超标")
            if "溶解氧" in text or "DO" in text:
                parts.append("溶解氧低")

        # 始终添加通用关键词确保覆盖面
        if not parts:
            return "水污染治理 城市河道 水质改善"

        query = " ".join(parts) + " 治理方案 城市河道"
        return query


# ---- 模块级便捷函数 ----

def get_rag_engine(persist_dir: str | None = None) -> RAGEngine:
    """获取 RAGEngine 单例。"""
    if RAGEngine._instance is None:
        RAGEngine._instance = RAGEngine(persist_dir)
    return RAGEngine._instance


def search_treatment_cases(
    query: str,
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> str:
    """检索相似污染治理案例（MCP 工具和 app.py 的共用接口）。

    Args:
        query: 检索查询（自然语言描述）。
        top_k: 返回案例数量。
        filters: 可选元数据过滤条件。

    Returns:
        JSON 字符串，包含检索到的案例列表。
    """
    engine = get_rag_engine()
    engine._collection = None  # 强制重新从磁盘读取，避免 Singleton 缓存过期状态
    engine._chroma_client = None  # 也重置 chroma 客户端，避免 SQLite 文件锁残留
    results = engine.retrieve(query, top_k, filters)

    if not results:
        return json.dumps(
            {"状态": "知识库为空", "建议": "请先运行 python scripts/build_rag_index.py 构建索引"},
            ensure_ascii=False,
        )

    # 简化输出，只保留关键字段
    simplified = []
    for hit in results:
        simplified.append({
            "标题": hit["metadata"].get("title", ""),
            "地点": hit["metadata"].get("location", ""),
            "污染程度": hit["metadata"].get("severity", ""),
            "治理方法": hit["metadata"].get("treatment_methods", ""),
            "相似度": round(1 - hit["distance"], 4),
            "内容摘要": hit["document"][:300] + "..." if len(hit["document"]) > 300 else hit["document"],
        })

    return json.dumps(
        {"检索结果": simplified, "共检索到": len(simplified), "查询": query},
        ensure_ascii=False,
    )
