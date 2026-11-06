<script setup>
import {ref} from 'vue'
import InvestigationBoard from '../InvestigationBoard.vue'
const editing=ref(false)
</script>
<template><div><h2>把线索变成待确认的复测计划</h2><p>在预警采样点附近及其上下游安排同一时段对照，优先复测本次异常指标，并核实仪器状态、流向和现场条件。没有证据时仅记录待核实假设。</p><p>以上是规则检测后的人工规划提示，并非 AI 生成结论。填写支持与反对依据，显式保存后才形成任务；计划不表示已经实施复测。</p><button class="simple-primary" @click="editing=!editing">{{editing?'收起计划编辑器':'编辑待确认的复测计划'}}</button><InvestigationBoard v-if="editing" /><RouterLink to="/chat#investigation-board">在专业版核验证据与任务 →</RouterLink></div></template>
