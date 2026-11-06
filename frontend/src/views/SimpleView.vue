<script setup>
import {ref,computed,watch} from 'vue'
import {useSessionStore} from '../stores/session'
import {useSimpleWorkflow} from '../composables/useSimpleWorkflow'
import SimpleStepper from '../components/simple/SimpleStepper.vue'
import SimpleDataEntry from '../components/simple/SimpleDataEntry.vue'
import SimpleResults from '../components/simple/SimpleResults.vue'
import SimpleAIInsight from '../components/simple/SimpleAIInsight.vue'
import SimpleNextActions from '../components/simple/SimpleNextActions.vue'
import ProjectPanel from '../components/ProjectPanel.vue'
import '../styles/simple.css'
const store=useSessionStore(),summary=useSimpleWorkflow(store),step=ref(store.status.has_data?1:0)
const aiVisited=ref(false)
watch(step,value=>{if(value===2)aiVisited.value=true})
const states=computed(()=>[summary.value.hasData?'已载入':'待开始',({idle:'待检测',loading:'检测中',ready:'已完成',stale:'已过期',failed:'失败'})[summary.value.detection],{idle:'待提问',loading:'执行中',ready:'已完成',failed:'失败',partial:'部分完成'}[summary.value.ai],'待人工确认'])
</script>
<template><div class="simple-page"><div class="simple-intro"><span class="simple-kicker">从观测出发 · 以证据为依据</span><h1>四步，读懂这段水质记录。</h1><p>先看监测与规则筛查，再核实证据、安排复测。</p><span class="simple-identity">{{summary.identity}} · 结果按当前项目与数据版本展示</span></div><SimpleStepper :step="step" :states="states" @select="step=$event" /><section class="simple-card" aria-live="polite"><SimpleDataEntry v-if="step===0" @done="step=1" /><SimpleResults v-else-if="step===1" /><SimpleNextActions v-else-if="step===3" /><SimpleAIInsight v-if="aiVisited" v-show="step===2" /></section><details class="simple-card"><summary>项目保存、打开与导出</summary><ProjectPanel /></details><details id="simple-help" class="simple-card"><summary>帮助与结果说明</summary><p>无 AI 密钥时，仍可查看规则结果、采样地图与趋势。预警和聚类是派生线索；仿真样例不代表现实河道污染。专业版提供完整证据及假定参数下的情景模拟。</p><p>两种模式共享项目与会话；切换不会清空数据或聊天草稿。保存项目后可在服务重启时恢复。</p></details></div></template>
