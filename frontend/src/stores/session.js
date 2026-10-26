import {textQuestionState,quoteKey,MAX_QUOTE} from '../utils/textQuestions'
import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'
import { isQuestionContext, questionContextMatches, contextQuestion } from '../utils/questionContext'

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
    sideRunning:false,textQuestions:textQuestionState(),
    chatDraft:'',questionContext:null,questionContextNotice:'',questionContextRequest:0,
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
    previewRequest: 0,
    investigationRevision: 0,
    selectedInvestigationId: '',
  }),

  getters: {
    keysReady: (s) => !!s.config.keys.deepseek,
    canAnalyze: (s) => s.status.has_data,
  },

  actions: {
    /** @param {Pick<import('../types/contracts').SelectedText,'text'|'source'|'title'>} selected
     * @param {'main'|'side'} target @param {import('../types/contracts').QuestionContextScope} [scope] */
    captureTextQuote(selected,target,scope) {
      if(!this.ready||this.switchingProject)return
      if(!selected.text.trim()||selected.text.length>MAX_QUOTE){this.textQuestions.error='引用须为1—3000字，请缩短选区。';return}
      const quote={...JSON.parse(JSON.stringify(selected)),project_id:scope?scope.project_id:this.projectState.current?.id??null,
        analysis_revision:scope?.analysis_revision??this.status.analysis_revision??0,
        results_revision:scope?.results_revision??this.status.results_revision??0}
      if(target==='main'){
        if(this.textQuestions.pendingMain&&quoteKey(this.textQuestions.pendingMain)===quoteKey(quote))return
        this.clearQuestionContext();this.textQuestions.pendingMain=quote
        this.questionContextNotice='已加入选中文字，发送时会核对来源；已有问题草稿保留。'
        if(!this.chatDraft.trim())this.chatDraft='请解释所选文字的含义、依据和适用限制。'
      }else{
        if(!this.textQuestions.pendingSide||quoteKey(this.textQuestions.pendingSide)!==quoteKey(quote)){
          this.textQuestions.pendingSide=quote;this.textQuestions.active=''
        }
        this.textQuestions.open=true;this.textQuestions.error=''
      }
    },
    async preparePendingQuote() {
      const quote=this.textQuestions.pendingMain
      if(!quote)return this.questionContext
      const workspace=this.workspaceRevision,key=quoteKey(quote)
      return this._run(async()=>{
        const card=await api.createQuestionContext({kind:'text_quote',text:quote.text,source:quote.source,
          project_id:quote.project_id,analysis_revision:quote.analysis_revision,results_revision:quote.results_revision})
        if(workspace!==this.workspaceRevision||this.switchingProject||!this.textQuestions.pendingMain||quoteKey(this.textQuestions.pendingMain)!==key)return null
        if(!isQuestionContext(card)||!questionContextMatches(card,this))throw Error('引用版本不匹配，请重新选择。')
        this.questionContext=card;this.textQuestions.pendingMain=null;this.questionContextNotice=''
        return card
      })
    },
    async prepareSideThread() {
      const q=this.textQuestions
      if(q.active)return q.threads.find(t=>t.id===q.active)||null
      const quote=q.pendingSide
      if(!quote)throw Error('请先选择一段文字。')
      const workspace=this.workspaceRevision,key=quoteKey(quote)
      return this._run(async()=>{
        const card=await api.createQuestionContext({kind:'text_quote',text:quote.text,source:quote.source,
          project_id:quote.project_id,analysis_revision:quote.analysis_revision,results_revision:quote.results_revision})
        if(workspace!==this.workspaceRevision||this.switchingProject||!q.pendingSide||quoteKey(q.pendingSide)!==key)return null
        const thread=await api.createSideThread(card.id)
        if(workspace!==this.workspaceRevision||this.switchingProject||!q.pendingSide||quoteKey(q.pendingSide)!==key)return null
        if(!q.threads.some(t=>t.id===thread.id))q.threads.push(thread)
        q.drafts[thread.id]=q.drafts[key]||'';q.active=thread.id
        return thread
      })
    },
    async loadSideThreads() {
      const workspace=this.workspaceRevision,project=this.projectState.current?.id,request=++this.textQuestions.request
      try{
        const value=await api.sideThreads()
        if(workspace!==this.workspaceRevision||project!==this.projectState.current?.id||request!==this.textQuestions.request||this.switchingProject)return
        const q=this.textQuestions
        q.threads=value.threads;q.run=value.run;q.notice=value.run.save_notice||''
        this.sideRunning=q.streaming||!!value.run.busy
        this.chatRunning=this.chatStreaming||this.sideRunning||!!this.agentRun?.busy
        if(!this.sideRunning&&value.run.run_id)await this.refreshProjects()
      }catch(e){if(workspace===this.workspaceRevision&&request===this.textQuestions.request)this.textQuestions.error='暂时无法核对侧边任务，请重试状态检查。'}
    },
    /** @param {string} [notice] */
    clearQuestionContext(notice='') {
      this.questionContextRequest++
      this.questionContext=null
      this.questionContextNotice=notice
    },
    /** @param {string} id */
    clearQuestionContextById(id) {
      if(this.questionContext?.id===id)this.clearQuestionContext()
    },
    /** @param {import('../types/contracts').QuestionContextSelector} selector
     * @returns {Promise<import('../types/contracts').QuestionContext|null>} */
    async prepareQuestionContext(selector) {
      if(!this.ready || this.switchingProject || (!this.status.has_data && selector.kind!=='rag_excerpt'))
        throw new Error('请先载入相应数据，或选择一份实际检索的资料片段。')
      const workspace=this.workspaceRevision, project=this.projectState.current?.id??null
      const revision=this.status.analysis_revision, results=this.status.results_revision??0
      if(typeof revision!=='number')throw new Error('数据版本尚未就绪，请刷新后重试。')
      return this._run(async()=>{
        const ticket=++this.questionContextRequest
        const current=()=>ticket===this.questionContextRequest && workspace===this.workspaceRevision
          && !this.switchingProject && project===(this.projectState.current?.id??null)
          && revision===this.status.analysis_revision && results===(this.status.results_revision??0)
        this.questionContext=null;this.questionContextNotice=''
        try {
          const card=await api.createQuestionContext({...selector,project_id:project,
            analysis_revision:revision,results_revision:results})
          if(!current())return null
          if(!isQuestionContext(card)||!questionContextMatches(card,this))throw new Error('选择的来源格式或版本不匹配，请重新选择。')
          this.questionContext=card
          if(!this.chatDraft.trim())this.chatDraft=contextQuestion(card.kind)
          return card
        } catch(error) {
          if(!current())return null
          this.questionContextNotice=errorMessage(error)
          throw error
        }
      })
    },
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
      this.chatRunning=this.chatStreaming || this.sideRunning || !!run?.busy
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
        this.clearQuestionContext()
        this.chatDraft=''
        this.textQuestions=textQuestionState();this.sideRunning=false
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
      this.statusRequest++; this.resultsRequest++; this.projectRequest++; this.previewRequest++
      this.questionContextRequest++
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
      if(this.questionContext && !questionContextMatches(this.questionContext,this))
        this.clearQuestionContext('数据或分析结果已更新，请重新选择上下文；问题草稿仍保留。')
      this.adoptAgentRun(status.agent_run || null)
      if (status.has_data) await this.refreshResults()
      else {
        this.blackSpots = this.crossValidation = this.comprehensive = null
        this.resultsWarnings = []
        this.previewRequest++
        this.preview = { columns:[], rows:[], total:0 }
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
      const request = ++this.previewRequest
      if (!this.status.has_data) {
        this.preview = { columns:[], rows:[], total:0 }
        return
      }
      const project = this.projectState.current?.id, workspace = this.workspaceRevision
      const revision = this.status.analysis_revision, switching = this.switchingProject
      const current = () => request === this.previewRequest && project === this.projectState.current?.id
        && workspace === this.workspaceRevision && revision === this.status.analysis_revision
        && switching === this.switchingProject && this.status.has_data
      try {
        const result = await api.preview(limit)
        if (current()) this.preview = result
      } catch (error) {
        if (current()) throw error
      }
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
