<script setup>
defineProps({step:Number,states:Array});defineEmits(['select'])
const labels=['导入数据','异常检测','AI 研判','复测建议']
</script>
<template><nav class="simple-steps" aria-label="分析步骤"><button v-for="(label,i) in labels" :key="label" :aria-current="step===i?'step':undefined" @click="$emit('select',i)"><span>0{{i+1}}</span><strong>{{label}}</strong><small>{{states[i]}}</small></button></nav></template>
