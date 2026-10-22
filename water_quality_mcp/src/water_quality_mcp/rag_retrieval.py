"""有界的词面/向量融合。只使用查询与索引内容，不含案例名单或评测答案。"""
from collections import Counter
import json
import math
import re
import unicodedata

STRATEGY = 'hybrid-rrf-v1'
MAX_LEXICAL_CHUNKS = 2000
CANDIDATES = 80
RRF_K = 60
RAG_RULES = ('治理案例是本地整理参考，原始发布内容未独立核验，不是本次河道实测。'
    '引用具体方法或数值时须标出工具实际返回的证据ID，并与相应原文片段一致；'
    '只有标题、元数据或截断摘要不能支持未出现的具体数值。'
    '检索结果与相似度不证明当前问题已有答案，不是污染来源或责任概率。'
    '资料未提供所问事实、时段或对象时须说明缺失；不能用历史案例编造实时值或责任结论。')


def tokens(text):
    text = unicodedata.normalize('NFKC', str(text)).lower()
    words = re.findall(r'[a-z0-9]+(?:[.][0-9]+)?', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
    return words


def source_key(hit):
    # 未提供来源时不凭标题合并两个不同文档。
    return str(hit.get('metadata', {}).get('source_file') or hit['id']).replace('\\', '/')


def lexical_rank(query, corpus, metadata=True, exclude_tokens=()):
    queries = set(tokens(query))-set(exclude_tokens)
    if not queries:
        queries = set(tokens(query))
    vectors = []
    for hit in corpus:
        meta = hit.get('metadata', {})
        prefix = ([str(meta.get('title', ''))]*3 +
            [str(meta.get(k, '')) for k in ('water_body_type', 'pollutant_types')]) if metadata else []
        text = ' '.join(prefix+[hit.get('document', '')])
        vectors.append(Counter(tokens(text)))
    if not vectors or not queries:
        return []
    avg = sum(sum(v.values()) for v in vectors)/len(vectors) or 1
    dfs = {q: sum(q in vector for vector in vectors) for q in queries}
    ranked = []
    for hit, vector in zip(corpus, vectors):
        score = 0.
        length = sum(vector.values())
        for term in sorted(queries):
            tf = vector[term]
            if tf:
                idf = math.log(1+(len(vectors)-dfs[term]+.5)/(dfs[term]+.5))
                score += idf*tf*2.2/(tf+1.2*(.25+.75*length/avg))
        if score > 0:
            ranked.append((score, hit))
    return [hit for _, hit in sorted(ranked, key=lambda pair: (-pair[0], str(pair[1]['id'])))[:CANDIDATES]]


def fuse_cases(query, dense, corpus, top_k, lexical_enabled=True):
    lexical = lexical_rank(query, corpus) if lexical_enabled else []
    by_id = {hit['id']: dict(hit) for hit in corpus}
    by_id.update({hit['id']: dict(hit) for hit in dense})
    scores = {}
    for ranking in (dense, lexical):
        for rank, hit in enumerate(ranking, 1):
            scores[hit['id']] = scores.get(hit['id'], 0.) + 1/(RRF_K+rank)
    ordered = sorted(scores, key=lambda ident: (-scores[ident], str(ident)))
    groups = {}
    for ident in ordered:
        hit = by_id[ident]
        groups.setdefault(source_key(hit), []).append(hit)
    result = []
    for members in list(groups.values())[:top_k]:
        best = dict(members[0])
        best['distance'] = best.get('distance')
        best['retrieval_score'] = scores[best['id']]
        best['retrieval_strategy'] = STRATEGY if lexical_enabled else 'dense-case-dedup (lexical corpus exceeds bound)'
        children = [h for h in corpus if source_key(h)==source_key(best)] or members
        meta = best.get('metadata',{})
        aspects = lexical_rank(query,children,metadata=False,
            exclude_tokens=tokens(str(meta.get('title',''))+' '+str(meta.get('water_body_type',''))))
        selected = []
        # 一个片段针对问题细节，另一个保留融合召回依据；不重复，也不按分块数量加分。
        for child in aspects[:1]+members+aspects:
            if child['id'] not in {h['id'] for h in selected}:
                selected.append(child)
            if len(selected)==2:break
        best['supporting_chunks'] = selected
        best['document'] = selected[0]['document']
        result.append(best)
    return result


def public_cases(hits):
    cases = []
    for hit in hits:
        meta = hit.get('metadata', {})
        distance = hit.get('distance')
        chunks = hit.get('supporting_chunks', [hit])
        snippets = []
        for chunk in chunks[:2]:
            if not chunk.get('id') or len(str(chunk['id']))>1000:
                continue
            doc = str(chunk.get('document', ''))
            heading = next((line for line in doc.splitlines() if line.startswith('#')), '')
            snippets.append({'证据ID': str(chunk['id']), '章节': heading[:120],
                '原文': doc[:1000], '截断': len(doc)>1000})
        doc = str(hit.get('document', ''))
        cases.append({'标题': str(meta.get('title', ''))[:300], '地点': str(meta.get('location', ''))[:200],
            '污染程度': str(meta.get('severity', ''))[:200], '治理方法': str(meta.get('treatment_methods', ''))[:500],
            '相似度': round(1-distance, 4) if isinstance(distance, (int,float)) and math.isfinite(distance) else None,
            '内容摘要': doc[:300]+('…（摘要截断，请查看证据片段）' if len(doc)>300 else ''),
            '来源文件': str(meta.get('source_file', '')).replace('\\','/')[:500],
            '来源说明': str(meta.get('source', '未提供来源说明'))[:300],
            '出处状态': '本地整理资料，原始发布内容未独立核验；未提供已核验原文链接。',
            '证据片段': snippets, '检索方法': hit.get('retrieval_strategy','dense-v1'),
            '排序分': hit.get('retrieval_score')})
    return cases


def compact_tool_result(text, limit=12000):
    """保留完整JSON与完整已返回片段；预算不足时减少案例并说明。"""
    if len(text)<=limit:
        return text
    data = json.loads(text)
    if not isinstance(data,dict) or not isinstance(data.get('检索结果'),list):
        return json.dumps({'状态':'检索返回超出显示预算；本次未提供可引用片段'},ensure_ascii=False)
    original = len(data['检索结果'])
    while data['检索结果']:
        data['共检索到'] = len(data['检索结果'])
        data['预算说明'] = f'上下文预算保留{len(data["检索结果"])}个案例，原返回{original}个；未提供的案例不可引用。'
        packed = json.dumps(data,ensure_ascii=False,separators=(',',':'))
        if len(packed)<=limit:
            return packed
        data['检索结果'].pop()
    return json.dumps({'检索结果':[],'共检索到':0,'预算说明':'片段超出上下文预算，本次未提供可引用案例'},ensure_ascii=False)
