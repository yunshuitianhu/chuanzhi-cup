<script setup>
import {onMounted,onBeforeUnmount} from 'vue'
import {useRouter} from 'vue-router'
import {useSessionStore} from '../../stores/session'
const store=useSessionStore(),router=useRouter()
let timer
onMounted(()=>{store.refreshAgentRun().catch(()=>{});timer=setInterval(()=>{if(store.chatRunning)store.refreshAgentRun().catch(()=>{})},3000)})
onBeforeUnmount(()=>clearInterval(timer))
function explain(){if(!store.chatDraft)store.chatDraft='请解释当前项目本次规则检测：列出关键发现、可核验的采样证据、反证与缺失信息，给出待确认的复测建议。仿真数据不得作为真实污染或企业责任依据。';router.push('/chat')}
</script>
<template><div><h2>让 AI 解释证据</h2><p>在共用专业对话中确认发送，查看工具进度、引用原文和完整回答。已有草稿会保留。</p><p v-if="!store.config.keys.deepseek">基础检测可用。AI 解释需要配置服务端模型密钥。</p><p v-if="store.chatRunning" role="status">当前 AI 任务执行中，请等待本次任务结束；切换模式后仍可查看进度。</p><p v-if="store.agentRun">当前任务状态：{{store.agentRun.status}}。完整进度与停止操作在专业对话中。</p><p v-if="store.agentConnectionError" role="alert">{{store.agentConnectionError}}</p><button class="simple-primary" :disabled="!store.status.has_data||!store.config.keys.deepseek||store.chatRunning" @click="explain">让 AI 解释本次检测</button><RouterLink to="/chat">查看已有回答与支持证据 →</RouterLink><p>调用未配置、失败或中断时不会显示为已完成；规则检测结果仍可查看。</p></div></template>
