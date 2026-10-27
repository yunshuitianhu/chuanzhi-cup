<script setup>
import {onMounted,onBeforeUnmount,ref,nextTick,watch} from 'vue'
import {useRouter,useRoute} from 'vue-router'
import {useSessionStore} from '../stores/session'
import {selectedPassage,MAX_QUOTE} from '../utils/textQuestions'
const store=useSessionStore(),router=useRouter(),route=useRoute()
const selected=ref(null),position=ref({}),menu=ref(null),error=ref('')
let raf=0
function hide(){cancelAnimationFrame(raf);raf=0;selected.value=null;error.value=''}
function inspect(){
  const selection=window.getSelection()
  try{selected.value=selectedPassage(selection,{path:route.path,title:route.meta.title})}catch{selected.value=null}
  error.value=''
  if(!selected.value)return
  if(selected.value.text.length>MAX_QUOTE){error.value='选中文字超过3000字，请缩短选区；不会自动截断。'}
  const box=selection.getRangeAt(0).getBoundingClientRect()
  position.value={left:Math.max(8,Math.min(window.innerWidth-310,box.left))+'px',top:Math.max(8,Math.min(window.innerHeight-110,box.bottom+8))+'px'}
}
function changed(){cancelAnimationFrame(raf);raf=requestAnimationFrame(inspect)}
async function use(target){
  if(!selected.value||error.value)return
  store.captureTextQuote(selected.value,target);hide()
  if(target==='main')await router.push('/chat')
}
function key(event){
  if(event.key==='Escape'){hide();return}
  if(event.altKey&&event.key.toLowerCase()==='q'){
    event.preventDefault();inspect();nextTick(()=>menu.value?.querySelector('button')?.focus())
  }
}
function outside(event){if(menu.value?.contains(event.target))return;if(!window.getSelection()?.toString())hide()}
onMounted(()=>{document.addEventListener('selectionchange',changed);document.addEventListener('keydown',key);document.addEventListener('pointerdown',outside);window.addEventListener('scroll',hide,true);window.addEventListener('resize',hide)})
onBeforeUnmount(()=>{cancelAnimationFrame(raf);document.removeEventListener('selectionchange',changed);document.removeEventListener('keydown',key);document.removeEventListener('pointerdown',outside);window.removeEventListener('scroll',hide,true);window.removeEventListener('resize',hide)})
watch(()=>[store.workspaceRevision,store.switchingProject,route.path],hide)
</script>
<template>
  <Teleport to="body"><div v-if="selected" ref="menu" class="selection-menu" :style="position" role="toolbar" aria-label="文字引用操作" @pointerdown.prevent>
    <template v-if="!error"><button type="button" @click="use('main')">添加到对话</button><button type="button" @click="use('side')">在侧边聊天中提问</button></template>
    <p v-else role="status">{{error}}</p><button type="button" aria-label="关闭引用菜单" @click="hide">×</button>
  </div></Teleport>
</template>
<style scoped>
.selection-menu{position:fixed;z-index:1000;max-width:calc(100vw - 16px);display:flex;gap:2px;flex-wrap:wrap;padding:5px;background:#243844;border:1px solid #486473;border-radius:8px;box-shadow:0 7px 25px #0006}.selection-menu button{font:inherit;font-size:12px;border:0;border-radius:5px;background:transparent;color:#edf5fa;padding:8px;min-height:36px}.selection-menu button:hover,.selection-menu button:focus-visible{background:#365566}.selection-menu p{font-size:12px;margin:5px;max-width:240px;color:#ffd19c}
</style>
