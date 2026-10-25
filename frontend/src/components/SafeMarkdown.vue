<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { renderReportMarkdown } from '../utils/reportMarkdown'

const props = defineProps({ content: { type: String, default: '' } })
const emit = defineEmits(['rendered'])
const displayed = ref(props.content)
let timer = null
// Batch streamed tokens rather than reparsing the growing report on every token.
watch(() => props.content, () => {
  if (timer !== null) return
  timer = setTimeout(() => { timer = null; displayed.value = props.content }, 80)
})
onBeforeUnmount(() => clearTimeout(timer))
const html = computed(() => renderReportMarkdown(displayed.value))
watch(displayed, async () => { await nextTick(); emit('rendered') })
</script>

<template>
  <div class="report-markdown" v-html="html"></div>
</template>

<style scoped>
.report-markdown { line-height:1.85; font-size:14px; overflow-wrap:anywhere; min-width:0; }
.report-markdown :deep(> :first-child) { margin-top:0; }
.report-markdown :deep(> :last-child) { margin-bottom:0; }
.report-markdown :deep(h1),.report-markdown :deep(h2),.report-markdown :deep(h3),.report-markdown :deep(h4) { line-height:1.5; margin:1.3em 0 .55em; color:var(--c-ink); }
.report-markdown :deep(h1) { font-size:1.4em; }.report-markdown :deep(h2) { font-size:1.25em; border-bottom:1px solid var(--c-border); padding-bottom:.4em; }
.report-markdown :deep(h3),.report-markdown :deep(h4) { font-size:1.1em; }
.report-markdown :deep(p) { margin:.7em 0; }
.report-markdown :deep(ul),.report-markdown :deep(ol) { padding-left:1.6em; margin:.65em 0; }
.report-markdown :deep(li + li) { margin-top:.3em; }
.report-markdown :deep(a) { color:#69c5ff; text-decoration:underline; text-underline-offset:3px; }
.report-markdown :deep(blockquote) { margin:1em 0; border-left:3px solid var(--c-accent); padding:.2em 1em; color:var(--c-sub); background:var(--c-bg); }
.report-markdown :deep(code) { background:var(--c-bg); border-radius:4px; padding:2px 5px; font-size:.9em; }
.report-markdown :deep(pre) { max-width:100%; overflow:auto; white-space:pre; padding:12px; background:var(--c-bg); border:1px solid var(--c-border); border-radius:7px; }
.report-markdown :deep(pre code) { padding:0; background:none; }
.report-markdown :deep(pre.report-plain) { white-space:pre-wrap; overflow-wrap:anywhere; }
.report-markdown :deep(.report-table) { max-width:100%; overflow-x:auto; margin:1em 0; }
.report-markdown :deep(table) { border-collapse:collapse; font-size:13px; min-width:100%; }
.report-markdown :deep(th),.report-markdown :deep(td) { padding:8px 10px; border:1px solid var(--c-border); text-align:left; min-width:90px; }
.report-markdown :deep(th) { background:var(--c-bg); }.report-markdown :deep(hr) { border:0; border-top:1px solid var(--c-border); margin:1.3em 0; }
</style>
