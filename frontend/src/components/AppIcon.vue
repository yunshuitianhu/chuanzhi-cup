<script setup>
defineProps({name:{type:String,default:'river'}})
const paths={
 chat:'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 1-2M8 9h8M8 13h5',
 charts:'M4 3v17h17M9 15V9m5 6V5m5 10v-4',
 river:'M3 6c4-4 6 4 10 0s6 0 8 0M3 12c4-4 6 4 10 0s6 0 8 0M3 18c4-4 6 4 10 0s6 0 8 0',
 tech:'m9 3 1 1v5l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V4l1-1M8 3h8M8 14h8',
 panel:'M4 4h16v16H4ZM9 4v16',
}
</script>
<template><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path :d="paths[name]||paths.river" /></svg></template>
