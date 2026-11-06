<script setup>
import {onMounted,ref} from 'vue'
import {useSessionStore} from '../../stores/session'
const store=useSessionStore(),error=ref('')
const labels={datetime:'采样时间',latitude:'纬度',longitude:'经度',ammonia_nitrogen:'氨氮 mg/L',dissolved_oxygen:'溶解氧 mg/L',turbidity:'浊度 NTU',ph:'pH',cod:'COD mg/L'}
onMounted(async()=>{if(!store.preview.rows.length)try{await store.refreshPreview()}catch(e){error.value=e.message}})
const text=value=>value==null?'缺测':typeof value==='number'?Number(value.toFixed(4)):String(value)
</script>
<template><section class="simple-table"><h3>监测数据表格</h3><p>显示前 {{store.preview.rows.length}} 条，共 {{store.preview.total}} 条导入记录。插值与质控标记可在地图点位详情中核对。</p><p v-if="error" role="alert">{{error}}</p><div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的监测记录表"><table><thead><tr><th scope="col">序号</th><th v-for="column in store.preview.columns" :key="column" scope="col">{{labels[column]||column}}</th></tr></thead><tbody><tr v-for="(row,i) in store.preview.rows" :key="i"><th scope="row">{{i+1}}</th><td v-for="column in store.preview.columns" :key="column">{{text(row[column])}}</td></tr></tbody></table></div></section></template>
<style scoped>
.table-scroll{max-width:100%;max-height:360px;overflow:auto;border:1px solid #365366;border-radius:8px}table{width:100%;border-collapse:collapse;font-size:14px;white-space:nowrap}th,td{padding:11px 14px;text-align:left;border-bottom:1px solid #2a4351}thead th{position:sticky;top:0;background:#203b4a;color:#d5eceb}tbody tr:nth-child(even){background:#203b4a44}tbody tr:hover{background:#31556466}
</style>
