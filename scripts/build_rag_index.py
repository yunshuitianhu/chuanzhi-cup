#!/usr/bin/env python
"""一键构建/重建 RAG 知识库索引。

从 knowledge_base/ 目录加载所有 Markdown 治理案例文档，
生成中文语义嵌入向量，存入 ChromaDB 持久化索引。

用法：
    python scripts/build_rag_index.py              # 重建索引
    python scripts/build_rag_index.py --no-reset    # 增量更新（不清空已有索引）
    python scripts/build_rag_index.py ./my_docs     # 指定知识库目录
"""

import argparse
import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from water_quality_mcp.src.water_quality_mcp.knowledge_loader import (
    DEFAULT_KNOWLEDGE_DIR,
    KnowledgeBaseManager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="构建/重建 RAG 知识库索引 (ChromaDB + sentence-transformers)"
    )
    parser.add_argument(
        "knowledge_dir",
        nargs="?",
        default=DEFAULT_KNOWLEDGE_DIR,
        help=f"知识库 Markdown 文档目录 (默认: {DEFAULT_KNOWLEDGE_DIR})",
    )
    parser.add_argument(
        "--persist-dir",
        default=None,
        help="ChromaDB 持久化目录 (默认: 项目根目录/.rag_index)",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="不清空已有索引，进行增量更新",
    )
    args = parser.parse_args()

    knowledge_dir = os.path.abspath(args.knowledge_dir)
    if not os.path.isdir(knowledge_dir):
        print(f"[ERROR] 知识库目录不存在: {knowledge_dir}")
        sys.exit(1)

    print("=" * 60)
    print("RAG 知识库索引构建工具")
    print("=" * 60)
    print(f"  知识库目录: {knowledge_dir}")
    print(f"  索引目录:   {args.persist_dir or '(默认: .rag_index)'}")
    print(f"  模式:       {'增量更新' if args.no_reset else '全量重建'}")
    print()

    t0 = time.time()

    manager = KnowledgeBaseManager(
        knowledge_dir=knowledge_dir,
        persist_dir=args.persist_dir,
    )

    # 先检查文档数量
    docs = manager.load_documents()
    if not docs:
        print("[WARN] 知识库目录中未找到有效 Markdown 文档（需含 frontmatter title）")
        print(f"   请先在 {knowledge_dir} 下创建案例文档，参考 _template.md")
        sys.exit(0)

    total_chunks = sum(len(d["chunks"]) for d in docs)
    print(f"[OK] 找到 {len(docs)} 个文档，将切分为 {total_chunks} 个语义块")
    print()

    # 构建索引
    count = manager.build_index(reset=not args.no_reset)

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print("[OK] 索引构建完成！")
    print(f"   文档数: {len(docs)}")
    print(f"   文档块: {count}")
    print(f"   耗时:   {elapsed:.1f}s")
    print(f"   索引位置: {manager._persist_dir or '(默认)'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
