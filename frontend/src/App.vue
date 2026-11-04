<script setup>
import { useRoute } from 'vue-router'
import WorkspaceShell from './WorkspaceShell.vue'

const route = useRoute()
</script>

<template>
  <RouterView v-if="route.name === 'welcome'" />
  <WorkspaceShell v-else-if="route.name" />
</template>
