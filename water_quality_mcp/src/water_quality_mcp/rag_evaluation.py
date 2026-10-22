"""固定资料查找评测；字符串支持检查不等于语义蕴含或现场事实认证。"""
import re
import unicodedata


def normalized(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(text))).lower()


def markdown_text(text):
    # 只移除配对的Markdown粗体与行内代码标记，不改数字、单位、比较符或措辞。
    text=re.sub(r'\*\*([^*\n]+)\*\*',r'\1',str(text))
    text=re.sub(r'__([^_\n]+)__',r'\1',text)
    return re.sub(r'`([^`\n]+)`',r'\1',text)


def retrieval_metrics(questions, rows):
    positives=[q for q in questions if q['relevant_sources']]
    ranks=[];covered=total=unique=returned=0
    for q in questions:
        snippets=rows[q['id']]
        sources=snippets['sources']
        unique+=len(set(sources));returned+=len(sources)
        if not q['relevant_sources']:continue
        ranks.append(next((i+1 for i,s in enumerate(sources[:5]) if s in q['relevant_sources']),None))
        for fact in q['evidence']:
            total+=1
            covered+=any(s['source_file']==fact['source_file'] and normalized(fact['required_text']) in normalized(s['text'])
                for s in snippets['context'])
    n=len(positives)
    return {'positive_questions':n,'hit_at_1':sum(r==1 for r in ranks)/n if n else None,
        'hit_at_5':sum(r is not None for r in ranks)/n if n else None,
        'mrr_at_5':sum(1/r for r in ranks if r)/n if n else None,
        'evidence_terms_covered':covered,'evidence_terms_total':total,
        'evidence_term_coverage':covered/total if total else None,
        'unique_source_ratio':unique/returned if returned else None}


def judge_answer(question, context, answer):
    """只验ID/原文及预标注要点；不把逐字引用冒充整句语义已核验。"""
    if not isinstance(answer,dict) or type(answer.get('answerable')) is not bool or not isinstance(answer.get('answer'),str) or not isinstance(answer.get('citations'),list):
        raise ValueError('模型回答结构无效')
    mapping={s['id']:s for s in context}
    valid=[];invalid=[];format_valid=[]
    for citation in answer['citations']:
        if not isinstance(citation,dict):
            invalid.append(citation);continue
        source=mapping.get(citation.get('id')) if isinstance(citation.get('id'),str) else None
        quote=citation.get('quote')
        if not source or not isinstance(quote,str) or not quote.strip() or normalized(quote) not in normalized(source['text']):
            invalid.append(citation)
        else:valid.append({**source,'quote':quote})
        if source and isinstance(quote,str) and quote.strip() and normalized(markdown_text(quote)) in normalized(markdown_text(source['text'])):
            format_valid.append({**source,'quote':quote})
    covered=sum(any(c['source_file']==f['source_file'] and normalized(f['required_text']) in normalized(c['quote'])
        for c in valid) for f in question['evidence'])
    format_covered=sum(any(c['source_file']==f['source_file'] and normalized(markdown_text(f['required_text'])) in normalized(markdown_text(c['quote']))
        for c in format_valid) for f in question['evidence'])
    return {'valid_citations':len(valid),'invalid_citations':len(invalid),
        'markdown_normalized_valid_citations':len(format_valid),'markdown_normalized_gold_terms_cited':format_covered,
        'markdown_normalized_all_gold_terms_cited':bool(question['evidence']) and format_covered==len(question['evidence']) and answer['answerable'],
        'gold_terms_cited':covered,'gold_terms_total':len(question['evidence']),
        'all_gold_terms_cited':bool(question['evidence']) and covered==len(question['evidence']) and answer['answerable'],
        'abstained':not answer['answerable'] and not answer['citations'],
        'scope':'quote matching and authored corpus labels only; semantic answer review remains separate'}
