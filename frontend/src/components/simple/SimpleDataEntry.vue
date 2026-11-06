<script setup>
import {ref,onMounted} from 'vue'
import {useSessionStore} from '../../stores/session'
const store=useSessionStore(),file=ref(null),sample=ref(''),error=ref('')
const emit=defineEmits(['done'])
onMounted(()=>store.loadSamples().catch(e=>{error.value=e.message}))
async function run(action){error.value='';try{await action();emit('done')}catch(e){error.value=e.message}}
</script>
<template><div><h2>从一张监测表开始</h2><p>上传走航监测表，或载入仿真样例开始。样例由模拟生成，不代表现实河道污染事实。</p><label>仿真样例<select v-model="sample"><option value="" disabled>请选择样例</option><option v-for="s in store.sampleCatalog" :key="s.id" :value="s.id">{{s.title}}</option></select></label><button class="simple-primary" :disabled="!sample||store.loading||store.chatRunning" @click="run(()=>store.loadSample(sample,true))">载入样例并检测</button><details><summary>上传自己的 CSV / Excel</summary><label>监测数据文件<input type="file" accept=".csv,.xlsx,.xls" @change="file=$event.target.files?.[0]||null" /></label><button :disabled="!file||store.loading||store.chatRunning" @click="run(()=>store.uploadData(file))">上传并读取</button></details><p v-if="store.loading" role="status">正在读取和处理数据…</p><p v-if="error" role="alert">{{error}}</p><p v-if="store.status.has_data">已加载：{{store.status.data_filename}} · {{store.status.rows}} 条</p></div></template>
