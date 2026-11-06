<script setup>
import {useSessionStore} from '../../stores/session'
import ChatView from '../../views/ChatView.vue'
const store=useSessionStore()
function explain(){if(!store.chatDraft)store.chatDraft='请解释当前项目本次规则检测：列出关键发现、可核验的采样证据、反证与缺失信息，给出待确认的复测建议。仿真数据不得作为真实污染或企业责任依据。'}
</script>
<template><div><h2>和 AI 一起核对本次检测</h2><p>直接在下方提问，确认发送后查看流式回答、执行进度和支持证据。</p><p v-if="!store.config.keys.deepseek">基础检测可用。AI 解释需要配置服务端模型密钥。</p><button class="simple-primary" :disabled="!store.status.has_data||!store.config.keys.deepseek||store.chatRunning" @click="explain">让 AI 解释本次检测</button><ChatView compact /></div></template>
