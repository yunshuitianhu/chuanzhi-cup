from openai import OpenAI

MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"


def _try_retrieve_treatment_cases(summary: str, top_k: int = 3) -> str:
    """尝试从 RAG 知识库检索相似治理案例。

    如果知识库为空或加载失败，返回空字符串（优雅降级）。
    """
    try:
        from water_quality_mcp.src.water_quality_mcp.rag_engine import (
            get_rag_engine,
        )
        engine = get_rag_engine()
        engine._collection = None  # 强制从磁盘刷新
        engine._chroma_client = None  # 也重置客户端，避免 SQLite 文件锁残留
        query = engine.build_query_from_summary(summary)
        results = engine.retrieve(query, top_k=top_k)
        return engine.format_context(results)
    except Exception:
        # RAG 未安装依赖或索引未构建 — 静默跳过
        return ""


def generate_report(api_key, summary):
    """基于 GB 3838-2002 生成水环境溯源报告。

    自动从 RAG 知识库检索相似治理案例作为参考，
    如果知识库不可用则降级为原始行为。
    """
    # RAG 检索：获取相似治理案例
    reference_cases = _try_retrieve_treatment_cases(summary, top_k=3)

    # 构造提示词（有案例则注入，无则保持原样）
    if reference_cases and "暂无相关治理案例" not in reference_cases:
        prompt = (
            "你是水环境溯源专家。请依据《城市黑臭水体整治工作指南》(2015)和GB 3838-2002撰写溯源报告。\n\n"
            "## 参考治理案例（从知识库检索的相似案例，仅借鉴治理思路和方法论）\n"
            f"{reference_cases}\n\n"
            "## 当前监测数据综合分析\n"
            f"{summary}\n\n"
            "撰写规则（严格遵守）：\n"
            "1. 报告的主体内容必须完全基于「当前监测数据综合分析」中的数据，不得使用参考案例中的数据（如案例中的氨氮值、COD值、治理周期、具体地点等）替代当前水体的实际情况。\n"
            "2. 参考案例仅供借鉴治理思路和技术路线，引用时必须注明来源，例如：参考类似案例，建议采用截污纳管与生态修复相结合的方式。\n"
            "3. 严禁将参考案例的地点、数据、治理效果直接当作当前水体的结论输出。当前水体的污染程度、地理位置、污染成因必须严格来源于监测数据。\n"
            "4. 治理建议应结合参考案例的治理方法，但需根据当前水体的实际污染物浓度、水体类型、周边环境做适配，不可照搬。"
        )
    else:
        prompt = (
            "你是水环境溯源专家。请依据《城市黑臭水体整治工作指南》(2015)和GB 3838-2002撰写溯源报告。\n"
            f"{summary}"
        )

    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=2000,
    )
    return resp.choices[0].message.content
