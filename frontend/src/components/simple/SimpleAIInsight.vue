<script setup>
import {useSessionStore} from '../../stores/session'
import ChatView from '../../views/ChatView.vue'
const store=useSessionStore()
function explain(){if(!store.chatDraft)store.chatDraft='请用通俗语言分析当前河道情况，按四项回答：河道污染情况、主要污染类型、最可能的污染源头、建议治理方案。给出本次记录支持的判断与具体行动；无法确定的部分明确说明原因，不编造源头或概率。仿真数据不得作为真实污染或企业责任依据。'}
</script>
<template><div><h2>和 AI 一起核对本次检测</h2><p>直接在下方提问，确认发送后查看流式回答、执行进度和支持证据。</p><p v-if="!store.config.keys.deepseek">基础检测可用。AI 解释需要配置服务端模型密钥。</p><button class="simple-primary" :disabled="!store.status.has_data||!store.config.keys.deepseek||store.chatRunning" @click="explain">让 AI 解释本次检测</button><ChatView compact /></div></template>
