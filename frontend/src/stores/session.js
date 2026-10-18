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
    workspaceRevision: 0,
    switchingProject: false,
    projectState: { projects:[], current:null, river_ui:{}, model:null, restore_error:'' },
    riverForm: null,

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
    resultsWarnings: [],
    statusRequest: 0,
    resultsRequest: 0,
    projectRequest: 0,
  }),

  getters: {
    keysReady: (s) => !!s.config.keys.deepseek,
    canAnalyze: (s) => s.status.has_data,
  },

  actions: {
    async refreshProjects() {
      const request = ++this.projectRequest
      const workspace = this.workspaceRevision
      const state = await api.projects()
      if (request !== this.projectRequest || workspace !== this.workspaceRevision) return
      this.projectState = state
      if (this.riverForm === null) this.riverForm = this.projectState.river_ui?.form || null
    },

    async adoptProject(state) {
      this.switchingProject = true
      try {
        this.projectState = state
        this.riverForm = state.river_ui?.form || null
        this.preview = { columns:[], rows:[], total:0 }
        this.blackSpots = this.crossValidation = this.comprehensive = null
        this.resultsWarnings = []
        this.chatMessages = []
        this.config = await api.config()
        await this.refreshStatus()
        this.chatMessages = (await api.chatHistory()).messages
        if (this.status.has_data) await this.refreshPreview()
        this.workspaceRevision++
      } catch (e) {
        this.ready = false
        throw e
      } finally { this.switchingProject = false }
    },

    async createProject(name, fromCurrent) {
      return this._run(() => this.changeProject(() => api.createProject({ name, from_current:fromCurrent, river_form:this.riverForm })))
    },

    async saveProject() {
      return this._run(async () => { this.projectState = await api.saveProject({river_form:this.riverForm}) })
    },

    async openProject(id) {
      return this._run(() => this.changeProject(() => api.openProject(id,this.riverForm)))
    },

    async importProject(file) {
      return this._run(() => this.changeProject(() => api.importProject(file,this.riverForm)))
    },

    async changeProject(request) {
      this.statusRequest++; this.resultsRequest++; this.projectRequest++
      this.switchingProject = true
      try { await this.adoptProject(await request()) }
      finally { this.switchingProject = false }
    },

    async exportProject() {
      return this._run(async () => {
        const blob = await api.exportProject(this.projectState.current.id,this.riverForm)
        await this.refreshProjects()
        return blob
      })
    },

    async bootstrap() {
      try {
        await api.health()
        this.config = await api.config()
        await this.refreshStatus()
        const history = await api.chatHistory()
        this.chatMessages = history.messages
        await this.refreshProjects()
        this.ready = true
        if (this.status.has_data) await this.refreshPreview()
      } catch (e) {
        this.ready = false
        this.error = `无法连接后端：${e.message}`
      }
    },

    async _run(fn) {
      if (this.loading || this.chatRunning) throw new Error('当前任务尚未完成，请稍后重试')
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
      const request = ++this.statusRequest
      const project = this.projectState.current?.id
      const workspace = this.workspaceRevision
      const status = await api.status()
      if (request !== this.statusRequest || project !== this.projectState.current?.id || workspace !== this.workspaceRevision) return
      if (status.analysis_revision !== this.status.analysis_revision) {
        this.chatMessages = []
        this.blackSpots = null
        this.crossValidation = null
        this.comprehensive = null
        this.riverForm = null
        await this.refreshProjects()
      }
      if (request !== this.statusRequest || workspace !== this.workspaceRevision) return
      if (status.results_revision !== this.status.results_revision) {
        this.blackSpots = this.crossValidation = this.comprehensive = null
      }
      this.status = status
      if (status.has_data) await this.refreshResults()
      else {
        this.blackSpots = this.crossValidation = this.comprehensive = null
        this.resultsWarnings = []
      }
    },

    async refreshResults() {
      const request = ++this.resultsRequest
      const project = this.projectState.current?.id
      const workspace = this.workspaceRevision
      const revision = this.status.analysis_revision
      const resultsRevision = this.status.results_revision
      const current = () => request === this.resultsRequest && project === this.projectState.current?.id
        && workspace === this.workspaceRevision && revision === this.status.analysis_revision
        && resultsRevision === this.status.results_revision
      try {
        const saved = await api.analysisResults()
        if (!current() || saved.analysis_revision !== revision || saved.results_revision !== resultsRevision) return
        this.blackSpots = saved.black_spots
        this.crossValidation = saved.cross_validation
        this.comprehensive = saved.comprehensive
        this.resultsWarnings = saved.warnings || []
      } catch (e) {
        if (current()) this.resultsWarnings = [`已保存分析结果加载失败：${e.message}。可刷新重试。`]
      }
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
        this.crossValidation = this.comprehensive = null
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
          this.riverForm = null
          await this.refreshProjects()
        }
        await this.refreshStatus()
        return res
      })
    },

    async runBlackSpots() {
      return this._run(async () => {
        this.blackSpots = await api.blackSpots()
        this.comprehensive = null
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
