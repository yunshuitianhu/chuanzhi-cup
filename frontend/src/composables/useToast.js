import { ref } from 'vue'

export const toasts = ref([])
let seq = 0

export function pushToast(text, type = 'ok', ms = 3200) {
  const id = ++seq
  toasts.value.push({ id, text: String(text), type })
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, ms)
}

export const toastOk = (t) => pushToast(t, 'ok')
export const toastErr = (t) => pushToast(t, 'error', 5200)
export const toastWarn = (t) => pushToast(t, 'warn', 4200)
