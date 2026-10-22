from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import app
from backend.agent.tools import truncate
from water_quality_mcp.src.water_quality_mcp.rag_engine import RAGEngine,search_treatment_cases
from water_quality_mcp.src.water_quality_mcp.rag_retrieval import fuse_cases,public_cases,compact_tool_result,MAX_LEXICAL_CHUNKS
from water_quality_mcp.src.water_quality_mcp.rag_evaluation import judge_answer,retrieval_metrics


def hit(ident,source,text,title='治理参考',distance=.2):
    return {'id':ident,'document':text,'metadata':{'source_file':source,'title':title,'source':'本地整理','water_body_type':'城市河道'},'distance':distance}


class RagRetrievalTests(unittest.TestCase):
    def test_multiple_chunks_of_one_document_do_not_fill_case_slots(self):
        a=hit('a0','a.md','## 概况\n示例河道氨氮偏高')
        b=hit('a1','a.md','## 效果\n示例河道氨氮降为1 mg/L')
        c=hit('b0','b.md','## 治理\n另一个河道治理')
        results=fuse_cases('河道氨氮治理',[a,b,c],[a,b,c],2)
        self.assertEqual(len(results),2)
        self.assertEqual({h['metadata']['source_file'] for h in results},{'a.md','b.md'})
        self.assertEqual(len(results[0]['supporting_chunks']),2)

    def test_question_detail_can_retrieve_a_child_not_in_dense_candidates(self):
        overview=hit('x0','x.md','## 概况\n港渠治理资料','港渠治理案例')
        detail=hit('x1','x.md','## 工程参数\n项目总投资为12万元。','港渠治理案例')
        result=fuse_cases('港渠治理项目总投资是多少',[overview],[overview,detail],1)[0]
        self.assertIn('x1',{c['id'] for c in result['supporting_chunks']})
        self.assertEqual({c['id'] for c in result['supporting_chunks']},{'x0','x1'})

    def test_unidentified_sources_are_not_merged_by_identical_title(self):
        a=hit('id1','','示例河道');b=hit('id2','','示例河道')
        self.assertEqual(len(fuse_cases('河道',[a,b],[a,b],2)),2)

    def test_source_excerpt_is_literal_bounded_and_has_no_invented_url(self):
        row=hit('file_0','folder/file.md','## 事实\n'+'原值'*700)
        case=public_cases([row])[0]
        snippet=case['证据片段'][0]
        self.assertEqual(snippet['原文'],row['document'][:1000]);self.assertTrue(snippet['截断'])
        self.assertEqual(snippet['证据ID'],'file_0')
        self.assertNotIn('url',case);self.assertIn('未独立核验',case['出处状态'])

    def test_lexical_only_hit_does_not_fabricate_a_vector_similarity(self):
        dense=hit('a','a.md','一般河道')
        rare=hit('b','b.md','稀有工艺说明','稀有工艺说明');rare.pop('distance')
        cases=public_cases(fuse_cases('稀有工艺',[dense],[dense,rare],2))
        self.assertIsNone(next(c for c in cases if c['来源文件']=='b.md')['相似度'])

    def test_context_budget_keeps_valid_json_and_whole_returned_snippets(self):
        rows=[hit(str(i),f'{i}.md','## 事实\n'+'说明'*500) for i in range(10)]
        original={'检索结果':public_cases(rows),'共检索到':10,'查询':'检索'}
        text=json.dumps(original,ensure_ascii=False)
        result=truncate('search_treatment_cases',text)
        packed=json.loads(result)
        self.assertLessEqual(len(result),12000)
        self.assertLess(packed['共检索到'],10);self.assertEqual(packed['共检索到'],len(packed['检索结果']))
        self.assertEqual(packed['检索结果'][0]['证据片段'],original['检索结果'][0]['证据片段'])
        self.assertIn('预算说明',packed)

    def test_unreasonable_query_or_count_is_rejected_before_index_access(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=RAGEngine(folder)
            for query,k in [('',5),(' '*5,5),('x'*1001,5),('河道',True),('河道',0),('河道',11),('河道',2.5)]:
                with self.assertRaises(ValueError):engine.retrieve(query,k)
            self.assertIsNone(engine._chroma_client)

    def test_metadata_filter_is_applied_to_both_retrieval_arms(self):
        row=hit('a','a.md','农村河道治理')
        seen=[]
        class Collection:
            def count(self):return 20
            def query(self,**kwargs):
                seen.append(('query',kwargs))
                return {'ids':[['a']],'documents':[[row['document']]],'metadatas':[[row['metadata']]],'distances':[[.2]]}
            def get(self,**kwargs):
                seen.append(('get',kwargs))
                return {'ids':['a'],'documents':[row['document']],'metadatas':[row['metadata']]}
        with tempfile.TemporaryDirectory() as folder:
            engine=RAGEngine(folder);engine._collection=Collection()
            engine._embedding_model=SimpleNamespace(encode=lambda *a,**k:SimpleNamespace(tolist=lambda:[1.,0.]))
            result=engine.retrieve('农村河道',5,{'water_body_type':'农村河道'})
        self.assertEqual(len(result),1)
        self.assertTrue(all(v['where']=={'water_body_type':'农村河道'} for _,v in seen))
        self.assertEqual(seen[1][1]['limit'],MAX_LEXICAL_CHUNKS+1)

    def test_no_filter_match_is_not_reported_as_an_empty_database(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=RAGEngine(folder)
            with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.get_rag_engine',return_value=engine),patch.object(engine,'retrieve',return_value=[]),patch.object(RAGEngine,'collection',new_callable=unittest.mock.PropertyMock) as collection:
                collection.return_value.count.return_value=5
                result=json.loads(search_treatment_cases('无此类型',filters={'water_body_type':'不存在'}))
        self.assertEqual(result['检索结果'],[]);self.assertEqual(result['共检索到'],0)
        self.assertNotIn('状态',result)

    def test_large_corpus_uses_explicit_bounded_fallback(self):
        row=hit('a0','a.md','## 示例\n河道治理')
        class Collection:
            def count(self):return 5000
            def query(self,**kwargs):
                return {'ids':[['a0']],'documents':[[row['document']]],'metadatas':[[row['metadata']]],'distances':[[.2]]}
            def get(self,**kwargs):
                self.limit=kwargs['limit'];n=self.limit
                return {'ids':[str(i) for i in range(n)],'documents':['其他内容']*n,'metadatas':[{}]*n}
        with tempfile.TemporaryDirectory() as folder:
            engine=RAGEngine(folder);engine._collection=Collection()
            engine._embedding_model=SimpleNamespace(encode=lambda *a,**k:SimpleNamespace(tolist=lambda:[1.,0.]))
            result=engine.retrieve('河道治理',5)
            self.assertEqual(engine.collection.limit,MAX_LEXICAL_CHUNKS+1)
        self.assertEqual(result[0]['id'],'a0')
        self.assertIn('exceeds bound',result[0]['retrieval_strategy'])

    def test_api_bad_input_is_a_validation_error_instead_of_500(self):
        client=TestClient(app)
        for body in [{'query':'河道','top_k':'abc'},{'query':'河道','top_k':True},{'query':'河道','top_k':0},
                {'query':'河道','top_k':11},{'query':123},{'query':'x'*1001},{'query':'河道','unexpected':1}]:
            with self.subTest(body=body):self.assertEqual(client.post('/api/rag/search',json=body).status_code,422)
        self.assertEqual(client.post('/api/rag/search',json={'query':'  '}).status_code,400)

    def test_api_can_return_no_matching_cases_without_failure(self):
        client=TestClient(app)
        with patch('backend.routers.rag.search_treatment_cases',return_value=json.dumps({'检索结果':[],'共检索到':0,'查询':'河道'})):
            self.assertEqual(client.post('/api/rag/search',json={'query':'河道'}).status_code,200)

    def test_evaluation_does_not_credit_foreign_id_wrong_number_or_wrong_source(self):
        question={'evidence':[{'source_file':'a.md','required_text':'12万元'}]}
        context=[{'id':'a0','source_file':'a.md','text':'**投资**：12万元'}]
        answer={'answerable':True,'answer':'12万元','citations':[{'id':'foreign','quote':'12万元'},{'id':'a0','quote':'15万元'},{'id':[],'quote':'12万元'}]}
        check=judge_answer(question,context,answer)
        self.assertEqual(check['invalid_citations'],3);self.assertFalse(check['all_gold_terms_cited'])

    def test_format_only_quote_mismatch_is_reported_separately_not_hidden(self):
        question={'evidence':[{'source_file':'a.md','required_text':'12万元'}]}
        context=[{'id':'a0','source_file':'a.md','text':'**投资**：12万元'}]
        answer={'answerable':True,'answer':'12万元','citations':[{'id':'a0','quote':'投资：12万元'}]}
        check=judge_answer(question,context,answer)
        self.assertEqual(check['valid_citations'],0);self.assertEqual(check['markdown_normalized_valid_citations'],1)
        self.assertFalse(check['all_gold_terms_cited']);self.assertTrue(check['markdown_normalized_all_gold_terms_cited'])

    def test_metrics_exclude_negative_questions_from_positive_denominators(self):
        questions=[{'id':'a','relevant_sources':['goal.md'],'evidence':[{'source_file':'goal.md','required_text':'12万元'}]},
            {'id':'b','relevant_sources':[],'evidence':[]}]
        rows={'a':{'sources':['other.md','goal.md'],'context':[{'source_file':'goal.md','text':'12万元'}]},'b':{'sources':['other.md'],'context':[]}}
        metrics=retrieval_metrics(questions,rows)
        self.assertEqual(metrics['positive_questions'],1);self.assertEqual(metrics['mrr_at_5'],.5)
        self.assertEqual(metrics['hit_at_5'],1);self.assertEqual(metrics['evidence_term_coverage'],1)


if __name__=='__main__':unittest.main()
