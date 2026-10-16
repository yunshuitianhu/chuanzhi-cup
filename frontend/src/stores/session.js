import { defineStore } from 'pinia'
import { api } from '../api/client'

/**
 * 会话状态 store（Pinia）。
 *
 * 对应原 app.py 的 st.session_state：数据、分析结果、配置集中在此，
 * 各视图组件从这里读取，避免层层传 props。
 */
export const useSessionStore = defineStore('session', {
  state: () => ({
    ready: false,            // 后端连通性
    loading: false,
    error: '',
    chatMessages: [],
    chatRunning: false,

    config: { keys: {}, target_water_class: 'V', valid_classes: [] },
    status: {
      has_data: false, rows: 0, video_frames: 0,
      has_black_spots: false, black_spot_count: 0,
      has_cross_validation: false, analysis_done: false,
    },
    preview: { columns: [], rows: [], total: 0 },

    blackSpots: null,        // { result, cluster_fields }
    crossValidation: null,
    comprehensive: null,
  }),

  getters: {
    keysReady: (s) => !!s.config.keys.deepseek,
    canAnalyze: (s) => s.status.has_data,
  },

  actions: {
    async bootstrap() {
      try {
        await api.health()
        this.config = await api.config()
        await this.refreshStatus()
        const history = await api.chatHistory()
        this.chatMessages = history.messages
        this.ready = true
        if (this.status.has_data) await this.refreshPreview()
      } catch (e) {
        this.ready = false
        this.error = `无法连接后端：${e.message}`
      }
    },

    async _run(fn) {
      this.loading = true
      this.error = ''
      try {
        return await fn()
      } catch (e) {
        this.error = e.message || String(e)
        throw e
      } finally {
        this.loading = false
      }
    },

    async refreshStatus() {
      const status = await api.status()
      if (status.analysis_revision !== this.status.analysis_revision) {
        this.chatMessages = []
        this.blackSpots = null
        this.crossValidation = null
        this.comprehensive = null
      }
      this.status = status
    },

    async refreshPreview(limit = 50) {
      if (!this.status.has_data) return
      this.preview = await api.preview(limit)
    },

    async uploadData(file) {
      return this._run(async () => {
        const res = await api.uploadData(file)
        await this.refreshStatus()
        await this.refreshPreview()
        // 新数据会清空既有分析，本地状态同步清掉
        this.blackSpots = null
        this.crossValidation = null
        this.comprehensive = null
        return res
      })
    },

    async uploadVideo(file) {
      return this._run(async () => {
        const res = await api.uploadVideo(file)
        await this.refreshStatus()
        return res
      })
    },

    async setTargetClass(cls) {
      return this._run(async () => {
        const res = await api.setTargetClass(cls)
        this.config = await api.config()
        if (res.analysis_reset) {
          this.blackSpots = null
          this.crossValidation = null
          this.comprehensive = null
        }
        await this.refreshStatus()
        return res
      })
    },

    async runBlackSpots() {
      return this._run(async () => {
        this.blackSpots = await api.blackSpots()
        await this.refreshStatus()
        return this.blackSpots
      })
    },

    async runCrossValidation(payload) {
      return this._run(async () => {
        this.crossValidation = await api.crossValidate(payload)
        await this.refreshStatus()
        return this.crossValidation
      })
    },

    async runComprehensive() {
      return this._run(async () => {
        this.comprehensive = await api.comprehensive()
        return this.comprehensive
      })
    },
  },
})
