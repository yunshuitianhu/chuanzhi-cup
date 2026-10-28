<script setup>
import {ref} from 'vue'
import {useSessionStore} from '../stores/session'
import {MAX_QUOTE} from '../utils/textQuestions'
const props=defineProps({source:{type:Object,default:null},title:{type:String,default:'所选文字'},sourceId:{type:String,default:''}})
const store=useSessionStore(),body=ref(null),error=ref('')
function reference(){
  const text=body.value?.innerText?.trim()
  if(!text||!props.source)return
  if(text.length>MAX_QUOTE){error.value='这段超过3000字，请选中其中需要解释的部分；不会自动截断。';return}
  error.value=''
  store.captureTextQuote({text,source:props.source,title:props.title},'side')
}
</script>
<template>
  <div class="quote-text">
    <div ref="body" :id="sourceId||undefined" tabindex="0" :data-quote-region="source?'text':undefined" :data-quote-source="source?JSON.stringify(source):undefined" :data-quote-title="title"><slot /></div>
    <button v-if="source" class="quote-equivalent" type="button" title="也可选中文字后按 Alt+Q 打开引用菜单" @click="reference">引用此段</button>
    <p v-if="error" class="quote-error" role="status">{{error}}</p>
  </div>
</template>
<style scoped>
.quote-text{min-width:0}.quote-equivalent{font:inherit;font-size:12px;background:transparent;color:var(--c-sub);border:1px solid var(--c-border);border-radius:5px;padding:4px 8px;margin-top:7px}.quote-error{font-size:12px;color:#f4bb87}.quote-text>div:focus-visible{outline:2px solid var(--c-accent);outline-offset:5px;border-radius:4px}
</style>
