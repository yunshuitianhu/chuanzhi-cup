import logging
import json

from openai import OpenAI
from .evidence import EVIDENCE_RULES

MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"

logger = logging.getLogger(__name__)


def _try_retrieve_treatment_cases(summary: str, top_k: int = 3) -> str:
    """尝试从 RAG 知识库检索相似治理案例。

    失败时返回空字符串并**记录日志**（不抛异常，保证报告生成仍可继续）。

    注: 这里曾经是完全静默的 `except Exception: return ""`。后果是
    build_query_from_summary 的键名不匹配（引发 TypeError）被长期掩盖,
    报告表面正常、实际从未注入过任何案例。改为记录 warning,
    同类问题下次会直接出现在日志里。
    """
    try:
        # 用包内相对导入, 而非 water_quality_mcp.src.water_quality_mcp.* 绝对路径。
        # 后者只在【项目根目录】位于 sys.path 且 src 不在其上时可解析;
        # 一旦 src 也在 sys.path 上(标准 src-layout 用法), water_quality_mcp
        # 会解析到内层包, 该绝对路径即失效。相对导入在两种配置下都成立。
        from .rag_engine import get_rag_engine

        engine = get_rag_engine()
        engine._collection = None  # 强制从磁盘刷新
        engine._chroma_client = None  # 也重置客户端，避免 SQLite 文件锁残留
        query = engine.build_query_from_summary(summary)
        results = engine.retrieve(query, top_k=top_k)
        return engine.format_context(results)
    except Exception as e:
        # RAG 未安装依赖 / 索引未构建 / 检索失败 — 降级为无案例, 但留下痕迹
        logger.warning("RAG 案例检索失败, 本次报告将不含参考案例: %s: %s",
                       type(e).__name__, e)
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

    # 事实抬头由代码生成，不让模型决定数据身份、GPS是否存在或是否已确认污染源。
    try:
        facts = json.loads(summary)
        facts = facts if isinstance(facts, dict) else {}
    except (ValueError, TypeError):
        facts = {}
    identity = facts.get('数据身份', {})
    identity = identity if isinstance(identity, dict) else {}
    label = identity.get('label', '来源未核验的数据')
    overview = facts.get('数据概览', {})
    screening = facts.get('黑臭检测', {})
    overview = overview if isinstance(overview, dict) else {}
    screening = screening if isinstance(screening, dict) else {}
    quality = facts.get('数据质量', {})
    header = [f"> 数据身份：{label}。",
              '> 结论级别：水质筛查与辅助排查；当前报告没有已确认的污染源或责任主体。']
    header.append('> 治理案例：' + ('已取得参考资料，仅借鉴治理方法。'
        if reference_cases and '暂无相关治理案例' not in reference_cases else '本次未取得可用案例，不补造引用。'))
    if overview:
        header.append(f"> 数据记录：{overview.get('记录数', '未提供')}；有效GPS记录：{overview.get('有效GPS记录数', '未核对')}。")
    if '黑点总数' in screening:
        header.append(f"> 点级预警：{screening['黑点总数']}；热点数量不能当作污染源数量。")
    if isinstance(quality, dict) and '需质控复核预警点数' in quality:
        header.append(f"> 其中需质控复核预警：{quality['需质控复核预警点数']}；仍保留全部预警，标记不能确认仪器故障。")
    prompt += ('\n\n请按数据事实、候选解释、证据缺口、复核建议组织正文。'
               '正文约1200字，不要完整复写全部工具表格，须保留局限和复核建议。'
               '不得确认因果来源；工具给出的时间趋势未判定时，只描述沿航迹读数变化。')
    with OpenAI(base_url=BASE_URL, api_key=api_key) as client:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{'role': 'system', 'content': '你是水质辅助分析报告撰写助手。' + EVIDENCE_RULES},
                      {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
        )
    finish_reason = getattr(resp.choices[0], 'finish_reason', None)
    if finish_reason not in ('stop', None):
        raise ValueError(f'报告未完整生成（{finish_reason}），不能标记为完成')
    body = resp.choices[0].message.content
    if not isinstance(body, str) or not body.strip():
        raise ValueError('报告未生成有效正文')
    logger.info('报告正文生成完成，finish_reason=%s', finish_reason)
    return '\n'.join(header) + '\n\n' + body
