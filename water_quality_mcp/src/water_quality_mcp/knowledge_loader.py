"""知识库文档加载、解析与索引构建。

将 knowledge_base/ 目录下的 Markdown 文档：
1. 解析 YAML frontmatter（提取元数据）
2. 按段落切分为语义块
3. 生成文本嵌入并存入 ChromaDB

用法：
    python -m water_quality_mcp.src.water_quality_mcp.knowledge_loader
    # 或：
    python scripts/build_rag_index.py
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录（相对于本文件：src/water_quality_mcp/knowledge_loader.py）
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
DEFAULT_KNOWLEDGE_DIR = os.path.join(_PROJECT_ROOT, "knowledge_base")

# frontmatter 正则：匹配 --- ... --- 块
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class KnowledgeBaseManager:
    """知识库管理器 — 加载文档、分块、构建 ChromaDB 索引。"""

    def __init__(
        self,
        knowledge_dir: str | None = None,
        persist_dir: str | None = None,
    ):
        self._knowledge_dir = os.path.abspath(
            knowledge_dir or DEFAULT_KNOWLEDGE_DIR
        )
        self._persist_dir = persist_dir  # None = 使用 rag_engine 默认值

    # ---- frontmatter 解析 ----

    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        """解析 Markdown 文件的 YAML frontmatter。

        使用轻量正则解析，避免依赖 PyYAML。
        仅支持简单类型：字符串、列表（[...]）、数字。

        Args:
            text: 完整的 Markdown 文本。

        Returns:
            (metadata_dict, body_text) — 元数据字典和正文（不含 frontmatter）。
        """
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return {}, text

        fm_text = m.group(1)
        body = text[m.end():]
        meta = {}

        for line in fm_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # key: value 或 key: [item1, item2, ...]
            kv_match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", line)
            if not kv_match:
                continue

            key = kv_match.group(1)
            value_str = kv_match.group(2).strip()

            # 列表：[a, b, c]
            list_match = re.match(r"^\[(.+)\]$", value_str)
            if list_match:
                items = [
                    item.strip().strip("'\"")
                    for item in list_match.group(1).split(",")
                    if item.strip()
                ]
                meta[key] = items
            else:
                # 去掉引号
                value_str = value_str.strip("'\"")
                # 尝试转为数字
                try:
                    if "." in value_str:
                        meta[key] = float(value_str)
                    else:
                        meta[key] = int(value_str)
                except ValueError:
                    meta[key] = value_str

        return meta, body

    # ---- 文档分块 ----

    @staticmethod
    def chunk_document(text: str, max_chars: int = 1000) -> list[str]:
        """将文档按段落切分为语义块。

        策略：
        1. 按 ## 标题分割为「节」
        2. 如果首节过短（仅含 H1 标题），将其合并到下一节
        3. 如果某节超过 max_chars，按双换行进一步切分
        4. 保证每块不超过 max_chars

        Args:
            text: 文档正文（已去除 frontmatter）。
            max_chars: 每块最大字符数。

        Returns:
            文档块列表。
        """
        # 按 ## 标题分割
        sections = re.split(r"\n(?=## )", text.strip())
        chunks = []

        # 合并过短的首节（仅含 H1 标题如 "# xxx\n\n"）到下一节
        # 避免第一个 chunk 内容摘要只有标题没有实质内容
        if len(sections) >= 2 and len(sections[0].strip()) < 50:
            sections[1] = sections[0].strip() + "\n\n" + sections[1].strip()
            sections = sections[1:]

        for section in sections:
            section = section.strip()
            if not section:
                continue

            if len(section) <= max_chars:
                chunks.append(section)
            else:
                # 按双换行进一步切分
                paragraphs = re.split(r"\n\n+", section)
                current_chunk = ""
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                        chunks.append(current_chunk.strip())
                        current_chunk = para
                    else:
                        if current_chunk:
                            current_chunk += "\n\n" + para
                        else:
                            current_chunk = para
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())

        return chunks

    # ---- 文档加载 ----

    def load_documents(self, glob_pattern: str = "**/*.md") -> list[dict[str, Any]]:
        """加载知识库目录下所有 Markdown 文档。

        Args:
            glob_pattern: 相对于 knowledge_dir 的匹配模式。

        Returns:
            文档列表，每项包含 file_path, filename, metadata, body, chunks。
        """
        pattern = os.path.join(self._knowledge_dir, glob_pattern)
        md_files = sorted(glob.glob(pattern, recursive=True))

        # 排除模板和 README
        md_files = [
            f for f in md_files
            if not os.path.basename(f).startswith("_")
            and os.path.basename(f) != "README.md"
        ]

        documents = []
        for file_path in md_files:
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception as e:
                logger.warning("读取文件失败: %s — %s", file_path, e)
                continue

            if not text.strip():
                continue

            meta, body = self.parse_frontmatter(text)
            if not meta.get("title"):
                logger.warning("文件缺少 frontmatter title，跳过: %s", file_path)
                continue

            chunks = self.chunk_document(body)
            relative_path = os.path.relpath(file_path, self._knowledge_dir)

            documents.append({
                "file_path": file_path,
                "relative_path": relative_path,
                "filename": os.path.basename(file_path),
                "metadata": meta,
                "body": body,
                "chunks": chunks,
            })

        logger.info("已加载 %d 个文档", len(documents))
        return documents

    # ---- 索引构建 ----

    def build_index(
        self,
        collection_name: str = "treatment_cases",
        reset: bool = True,
    ) -> int:
        """构建/重建 ChromaDB 索引。

        1. 加载所有 markdown 文档
        2. 分块
        3. 生成嵌入向量
        4. 存入 ChromaDB

        Args:
            collection_name: ChromaDB 集合名称。
            reset: 是否在构建前清空已有集合。

        Returns:
            入库的文档块总数。
        """
        from water_quality_mcp.src.water_quality_mcp.rag_engine import (
            RAGEngine,
            EMBEDDING_MODEL_NAME,
        )

        documents = self.load_documents()
        if not documents:
            logger.warning("知识库目录 %s 中未找到有效文档", self._knowledge_dir)
            return 0

        engine = RAGEngine(self._persist_dir)

        # 如果需要重置，删除旧集合
        if reset:
            try:
                engine.chroma_client.delete_collection(collection_name)
            except Exception:
                pass
            # 重新获取集合（会创建新的）
            engine._collection = None

        collection = engine.collection
        embedding_model = engine.embedding_model

        total_chunks = 0
        for doc in documents:
            meta = doc["metadata"]
            relative_path = doc["relative_path"]

            for i, chunk in enumerate(doc["chunks"]):
                chunk_id = f"{relative_path}_{i}"

                # 生成嵌入
                embedding = embedding_model.encode(
                    chunk, normalize_embeddings=True
                ).tolist()

                # 准备 ChromaDB 元数据（只存简单类型）
                chroma_meta: dict[str, str | int | float | bool] = {
                    "source_file": relative_path,
                    "chunk_id": chunk_id,
                }
                for key in ("title", "location", "severity", "water_body_type", "source"):
                    val = meta.get(key)
                    if isinstance(val, str):
                        chroma_meta[key] = val
                # 列表字段转为逗号分隔字符串（ChromaDB 支持 $contains）
                for key in ("pollutant_types", "treatment_methods"):
                    val = meta.get(key)
                    if isinstance(val, list):
                        chroma_meta[key] = ", ".join(val)

                # 插入 ChromaDB
                collection.add(
                    ids=[chunk_id],
                    embeddings=[embedding],
                    documents=[chunk],
                    metadatas=[chroma_meta],
                )
                total_chunks += 1

        logger.info(
            "索引构建完成: %d 个文档块已入库 (嵌入模型: %s)",
            total_chunks,
            EMBEDDING_MODEL_NAME,
        )
        return total_chunks


# ---- CLI ----

def main():
    """命令行入口：构建 RAG 索引。"""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    knowledge_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KNOWLEDGE_DIR
    persist_dir = sys.argv[2] if len(sys.argv) > 2 else None
    reset = "--no-reset" not in sys.argv

    print(f"知识库目录: {knowledge_dir}")
    print(f"索引目录:   {persist_dir or '(默认)'}")
    print(f"重建模式:   {'是' if reset else '否（增量）'}")
    print()

    manager = KnowledgeBaseManager(knowledge_dir, persist_dir)
    count = manager.build_index(reset=reset)
    print(f"\n✅ 索引完成：{count} 个文档块已入库")


if __name__ == "__main__":
    main()
