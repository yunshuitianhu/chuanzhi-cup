import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
test.after(async()=>{await server.close()})
const {default:Case}=await server.ssrLoadModule('/src/components/RagCase.vue')
const render=item=>renderToString(createSSRApp(Case,{item}))

test('治理资料显示来源、片段ID、原文及明确的截断状态',async()=>{
  const html=await render({'标题':'受控资料','相似度':.85,'来源说明':'本地整理','来源文件':'folder/case.md','出处状态':'未独立核验',
    '证据片段':[{'证据ID':'case.md_1','章节':'## 治理效果','原文':'DO=0 mg/L','截断':true}]})
  for(const text of ['case.md_1','folder/case.md','DO=0 mg/L','前1000字符','未独立核验','向量余弦 0.850'])assert.ok(html.includes(text),text)
  assert.doesNotMatch(html,/85%/)
})

test('资料自由文本作为文本转义，缺向量分不显示成零分',async()=>{
  const html=await render({'标题':'<script>attack()</script>','相似度':null,
    '证据片段':[{'证据ID':'safe','原文':'<img src=x onerror=attack()>','章节':'资料'}]})
  assert.doesNotMatch(html,/<script\b|<img\b|向量余弦/)
  assert.match(html,/&lt;img/)
})

test('旧工具结果只显示摘要，不能补造片段ID和核验状态',async()=>{
  const html=await render({'标题':'旧案例','相似度':.3,'内容摘要':'只保留摘要'})
  assert.match(html,/缺少可核对片段/);assert.match(html,/旧返回未提供来源说明/)
  assert.doesNotMatch(html,/case.md_1|已核验/)
})

test('技术评测表展示保留的失败和有限验证范围',async()=>{
  const {default:Evaluation}=await server.ssrLoadModule('/src/components/RagEvaluationTech.vue')
  const html=await renderToString(createSSRApp(Evaluation))
  for(const text of ['87.5%','100.0%','45.0%','90.0%','H02','H14','24次','独立专家复核','不是现场检测准确率'])assert.ok(html.includes(text),text)
  assert.match(html,/无检索方案不提供资料/)
})
