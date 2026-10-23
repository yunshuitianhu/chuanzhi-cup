import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'

/**
 * 会话状态 store（Pinia）。
 *
 * 对应原 app.py 的 st.session_state：数据、分析结果、配置集中在此，
 * 各视图组件从这里读取，避免层层传 props。
 */
export const useSessionStore = defineStore('session', {
  /** @returns {import('../types/contracts').SessionState} */
  state: () => ({
    ready: false,            // 后端连通性
    connecting: false,
    bootstrapStage: '',
    loading: false,
    error: '',
    chatMessages: [],
    chatRunning: false,
    chatStreaming: false,
    agentRun: null,
    agentRunAt: 0,
    agentRunRequest: 0,
    agentActiveId: null,
    agentConnectionError: '',
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
    investigationRevision: 0,
    selectedInvestigationId: '',
  }),

  getters: {
    keysReady: (s) => !!s.config.keys.deepseek,
    canAnalyze: (s) => s.status.has_data,
  },

  actions: {
    /** @param {string|null} id @param {import('../types/contracts').JsonObject} payload */
    async saveInvestigation(id,payload) {
      return this._run(async()=>{
        const workspace=this.workspaceRevision
        const result=id?await api.updateInvestigation(id,payload):await api.createInvestigation(payload)
        if(workspace!==this.workspaceRevision)throw new Error('项目已切换，排查结果未应用到当前页面，请重新载入核对。')
        this.selectedInvestigationId=result.task_id
        this.investigationRevision++
        // 已绑定项目的排查修改在后端同步保存；刷新版本号，避免下次另存冲突。
        if(this.projectState.current?.id) {
          try {await this.refreshProjects()}
          catch(e) {result.refresh_warning='记录已保存，但项目列表刷新失败，请刷新任务后核对项目版本。'}
        }
        return result
      })
    },
    /** @param {string} bundle @param {number} position */
    async investigateRecord(bundle,position) {
      return this._run(async()=>{
        const workspace=this.workspaceRevision
        const board=await api.investigations()
        if(workspace!==this.workspaceRevision)throw new Error('项目已切换，请从当前项目重新选择报告记录。')
        const result=await api.createInvestigation({scope_id:board.scope_id,expected_revision:board.revision,
          reference:{bundle_id:bundle,position}})
        if(workspace!==this.workspaceRevision)throw new Error('项目已切换，排查结果未应用到当前页面，请重新载入核对。')
        this.selectedInvestigationId=result.task_id
        this.investigationRevision++
        if(this.projectState.current?.id) {
          try {await this.refreshProjects()}
          catch(e) {result.refresh_warning='记录已保存，但项目列表刷新失败，请刷新任务后核对项目版本。'}
        }
        return result
      })
    },
    beginAgentStream() {
      if(this.chatRunning || this.loading || !this.ready)return false
      this.agentRunRequest++; this.statusRequest++; this.resultsRequest++
      this.chatStreaming=true; this.chatRunning=true
      this.agentRun=null; this.agentActiveId=null; this.agentConnectionError=''
      return true
    },
    /** @param {import('../types/contracts').AgentRun|null} run */
    adoptAgentRun(run) {
      if(run?.analysis_revision !== undefined && run.analysis_revision !== this.status.analysis_revision)return
      if(this.chatStreaming && this.agentActiveId && run?.run_id!==this.agentActiveId)return
      this.agentRun=run
      this.agentRunAt=Date.now()
      this.chatRunning=this.chatStreaming || !!run?.busy
    },
    async refreshAgentRun() {
      const request=++this.agentRunRequest, workspace=this.workspaceRevision, project=this.projectState.current?.id
      const current=()=>request===this.agentRunRequest && workspace===this.workspaceRevision && project===this.projectState.current?.id
      try {
        const run=await api.agentRun()
        if(!current())return
        const wasBusy=!!this.agentRun?.busy || this.chatRunning
        this.adoptAgentRun(run)
        this.agentConnectionError=''
        if(wasBusy && !run.busy && !this.chatStreaming) {
          await this.refreshStatus()
          const history=await api.chatHistory()
          if(current())this.chatMessages=history.messages
        }
      } catch(error) {
        if(current())this.agentConnectionError='暂时无法核对任务状态，请恢复连接后重试。'
        throw error
      }
    },
    async stopAgentRun() {
      const id=this.agentRun?.run_id, workspace=this.workspaceRevision
      if(!id || !this.agentRun?.busy)return
      const result=await api.stopAgentRun(id)
      if(workspace===this.workspaceRevision && this.agentRun?.run_id===id)this.adoptAgentRun(result)
    },
    /** @param {import('../types/contracts').QualityConfig} config */
    async updateQualityConfig(config) {
      return this._run(async () => {
        const response = await api.updateQualityConfig(config)
        await this.refreshStatus()
        return response
      })
    },
    async refreshProjects() {
      const request = ++this.projectRequest
      const workspace = this.workspaceRevision
      const state = await api.projects()
      if (request !== this.projectRequest || workspace !== this.workspaceRevision) return
      this.projectState = state
      if (this.riverForm === null) this.riverForm = this.projectState.river_ui?.form || null
    },

    /** @param {import('../types/contracts').ProjectState} state */
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

    /** @param {string} name @param {boolean} fromCurrent */
    async createProject(name, fromCurrent) {
      return this._run(() => this.changeProject(() => api.createProject({ name, from_current:fromCurrent, river_form:this.riverForm })))
    },

    async saveProject() {
      return this._run(async () => { this.projectState = await api.saveProject({river_form:this.riverForm}) })
    },

    /** @param {string} id */
    async openProject(id) {
      return this._run(() => this.changeProject(() => api.openProject(id,this.riverForm)))
    },

    /** @param {File} file */
    async importProject(file) {
      return this._run(() => this.changeProject(() => api.importProject(file,this.riverForm)))
    },

    /** @param {() => Promise<import('../types/contracts').ProjectState>} request */
    async changeProject(request) {
      this.statusRequest++; this.resultsRequest++; this.projectRequest++
      this.switchingProject = true
      try { await this.adoptProject(await request()) }
      finally { this.switchingProject = false }
    },

    async exportProject() {
      return this._run(async () => {
        const id = this.projectState.current?.id
        if (!id) throw new Error('请先保存项目后再导出')
        const blob = await api.exportProject(id,this.riverForm)
        await this.refreshProjects()
        return blob
      })
    },

    async bootstrap() {
      if (this.connecting || this.loading || this.chatRunning || this.switchingProject) return
      this.connecting = true
      this.ready = false
      this.error = ''
      try {
        this.bootstrapStage = '检查服务连接'
        await api.health(AbortSignal.timeout(10000))
        this.bootstrapStage = '读取配置'
        this.config = await api.config()
        this.bootstrapStage = '恢复数据与分析结果'
        await this.refreshStatus()
        this.bootstrapStage = '恢复对话'
        const history = await api.chatHistory()
        this.chatMessages = history.messages
        this.bootstrapStage = '读取项目目录'
        await this.refreshProjects()
        this.bootstrapStage = '读取数据预览'
        if (this.status.has_data) await this.refreshPreview()
        this.ready = true
        this.bootstrapStage = '已连接'
      } catch (e) {
        this.ready = false
        this.error = `启动检查失败（${this.bootstrapStage}）：${errorMessage(e)}`
      } finally { this.connecting = false }
    },

    /** @template T @param {() => Promise<T>} fn @returns {Promise<T>} */
    async _run(fn) {
      if (this.loading || this.chatRunning) throw new Error('当前任务尚未完成，请稍后重试')
      this.loading = true
      this.error = ''
      try {
        return await fn()
      } catch (e) {
        this.error = errorMessage(e)
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
      this.adoptAgentRun(status.agent_run || null)
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
        if (current()) this.resultsWarnings = [`已保存分析结果加载失败：${errorMessage(e)}。可刷新重试。`]
      }
    },

    /** @param {number} [limit] */
    async refreshPreview(limit = 50) {
      if (!this.status.has_data) return
      this.preview = await api.preview(limit)
    },

    /** @param {File} file */
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

    /** @param {File} file */
    async uploadVideo(file) {
      return this._run(async () => {
        const res = await api.uploadVideo(file)
        this.crossValidation = this.comprehensive = null
        await this.refreshStatus()
        return res
      })
    },

    /** @param {import('../types/contracts').WaterClass} cls */
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

    /** @param {import('../types/contracts').CrossValidationRequest} payload */
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
