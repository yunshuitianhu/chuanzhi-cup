<script setup>
import QuoteText from './QuoteText.vue'
import AskContext from './AskContext.vue'
defineProps({item:{type:Object,required:true},retrievalId:{type:String,default:''}})
</script>

<template>
  <article class="rag-case">
    <header><h4>{{item['标题']}}</h4><span v-if="Number.isFinite(item['相似度'])" class="score">向量余弦 {{item['相似度'].toFixed(3)}}</span></header>
    <p class="meta">{{item['地点']}} · {{item['污染程度']}}</p>
    <p v-if="item['治理方法']">治理方法：{{item['治理方法']}}</p>
    <p class="source">来源说明：{{item['来源说明']||'旧返回未提供来源说明'}}<br />{{item['出处状态']||'参考资料，原始发布内容未独立核验。'}}</p>
    <details v-if="item['证据片段']?.length">
      <summary>核对原文片段（{{item['证据片段'].length}}）</summary>
      <p class="meta">来源文件：{{item['来源文件']}} · {{item['检索方法']}}</p>
      <section v-for="snippet in item['证据片段']" :key="snippet['证据ID']">
        <p><strong>{{snippet['章节']}}</strong><br /><code>{{snippet['证据ID']}}</code></p>
        <QuoteText :source="retrievalId?{kind:'rag',retrieval_id:retrievalId,evidence_id:snippet['证据ID']}:null" :title="item['标题']+' · '+snippet['证据ID']"><pre>{{snippet['原文']}}</pre></QuoteText>
        <AskContext v-if="retrievalId && snippet['证据ID']" :selector="{kind:'rag_excerpt',retrieval_id:retrievalId,evidence_id:snippet['证据ID']}" label="围绕这段资料提问" />
        <p v-else class="source">该历史返回没有引用令牌，重新检索后可带来源提问。</p>
        <p v-if="snippet['截断']" class="source">该片段仅返回前1000字符，未展示部分不能据此引用。</p>
      </section>
    </details>
    <details v-else-if="item['内容摘要']"><summary>查看摘要（缺少可核对片段）</summary><p>{{item['内容摘要']}}</p></details>
  </article>
</template>

<style scoped>
.rag-case{background:var(--c-surface-2);border:1px solid var(--c-border);border-radius:9px;padding:13px 15px;min-width:0;font-size:12px;line-height:1.8}.rag-case header{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:8px}h4{margin:0;font-size:14px;flex:1;min-width:150px}.score{font-size:12px;color:var(--c-sub);white-space:nowrap}.meta,.source{color:var(--c-sub);font-size:12px}p{margin:7px 0}summary{cursor:pointer;color:var(--c-accent)}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;font:inherit;background:var(--c-bg);padding:10px;border-radius:6px}section{margin-top:12px;border-top:1px solid var(--c-border)}
</style>
