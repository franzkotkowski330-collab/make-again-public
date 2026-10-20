const service = require('../../utils/service')
const { showError, formatTime } = require('../../utils/ui')
Page({
  data: {
    hasGuest: false, messages: [], draft: '', canSend: false, saving: false, scrollTarget: '', keyboardHeight: 0,
    error: '', mode: 'text', guide: null, completed: 0, total: 4, progress: 0, quickReplies: [], draftLimit: 30,
    placeholder: '告诉我一个你喜欢的称呼…', recording: false, authorizing: false, cancelRecording: false,
    recordSeconds: 0, pendingAudio: null, playingId: '', audioSaving: false, dateLabel: ''
  },
  onShow() {
    this._active = true
    getApp().globalData.pendingChatGuide = false
    this.setData({ keyboardHeight: 0 })
    this.refresh(true)
  },
  onHide() { this.leave() },
  onUnload() {
    this.leave()
    if (this._recorder) {
      this._recorder.offStart(this._onRecordStart)
      this._recorder.offStop(this._onRecordStop)
      this._recorder.offError(this._onRecordError)
      this._recorder.offInterruptionBegin(this._onInterruption)
    }
    if (this._audio) this._audio.destroy()
  },
  leave() {
    this._active = false
    this._holding = false
    this._recordRequest = (this._recordRequest || 0) + 1
    this._discardRecording = true
    if (this._recorder && (this.data.recording || this._startingRecord)) this._recorder.stop()
    clearInterval(this._recordTimer)
    this.stopPlayback()
    this.setData({ keyboardHeight: 0, recording: false, authorizing: false })
  },
  refresh(start) {
    try {
      const overview = service.getOverview()
      if (!overview.hasGuest) { this.setData({ hasGuest: false, error: '' }); return }
      if (start) service.startChat()
      const state = service.getChatState()
      const messages = state.messages.map(message => Object.assign({}, message, { timeLabel: formatTime(message.createdAt) }))
      const d = new Date()
      this.setData({
        hasGuest: true, messages, guide: state.step, completed: state.completed, total: state.total,
        progress: state.completed / state.total * 100, quickReplies: state.step ? state.step.options : [], draftLimit: state.maxLength,
        placeholder: state.step ? state.step.placeholder : '写点什么，慢慢说…', error: '',
        dateLabel: d.getMonth() + 1 + '月' + d.getDate() + '日 ' + ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][d.getDay()],
        scrollTarget: messages.length ? 'msg-' + messages[messages.length - 1].id : ''
      })
    } catch (error) { this.setData({ error: error.message }) }
  },
  acceptConsent() {
    if (this.data.saving) return
    this.setData({ saving: true })
    try { service.acceptConsent(); this.refresh(true) } catch (error) { showError(error) }
    this.setData({ saving: false })
  },
  declineConsent() { wx.switchTab({ url: '/pages/home/home' }) },
  readPrivacy() { wx.navigateTo({ url: '/pages/privacy/privacy' }) },
  viewProfile() { wx.navigateTo({ url: '/pages/profile/edit' }) },
  retry() { this.refresh(true) },
  onInput(event) { const draft = event.detail.value; this.setData({ draft, canSend: !!draft.trim() }) },
  keyboardChange(event) { this.setData({ keyboardHeight: event.detail.height || 0 }) },
  blurInput() { wx.hideKeyboard() },
  toggleMode() {
    if (this.data.recording || this.data.authorizing || this.data.audioSaving || this.data.pendingAudio) return
    this.blurInput()
    this.setData({ mode: this.data.mode === 'text' ? 'voice' : 'text', keyboardHeight: 0 })
  },
  quickReply(event) {
    if (this.data.draft.trim() || this.data.pendingAudio) { wx.showToast({ title: '请先发送或清空正在编辑的内容', icon: 'none' }); return }
    this.submit({ text: event.currentTarget.dataset.value })
  },
  skipQuestion() {
    if (this.data.draft.trim() || this.data.pendingAudio) { wx.showToast({ title: '请先发送或清空正在编辑的内容', icon: 'none' }); return }
    if (this.data.guide) this.submit({ skip: true })
  },
  send() { if (this.data.canSend) this.submit({ text: this.data.draft }) },
  submit(payload) {
    if (this.data.saving || this.data.audioSaving || this.data.recording || this.data.authorizing) return false
    this.setData({ saving: true })
    try {
      service.sendChatMessage(payload)
      this.setData({ draft: '', canSend: false })
      this.refresh()
      return true
    } catch (error) { showError(error); return false }
    finally { this.setData({ saving: false }) }
  },
  setupRecorder() {
    if (this._recorder) return
    this._recorder = wx.getRecorderManager()
    this._onRecordStart = () => {
      this._startingRecord = false
      if (!this._holding || !this._active) { this._discardRecording = true; this._recorder.stop(); return }
      this.setData({ recording: true, authorizing: false, recordSeconds: 0 })
      this._recordTimer = setInterval(() => this.setData({ recordSeconds: Math.min(60, this.data.recordSeconds + 1) }), 1000)
    }
    this._onRecordStop = result => {
      clearInterval(this._recordTimer)
      this._startingRecord = false
      const discard = this._discardRecording || this.data.cancelRecording || !this._active
      this._holding = false
      this.setData({ recording: false, authorizing: false, cancelRecording: false })
      if (discard) return
      if (!result.tempFilePath || result.duration < 1000) { wx.showToast({ title: '说话时间太短，请至少录制 1 秒', icon: 'none' }); return }
      this.setData({ pendingAudio: { filePath: result.tempFilePath, duration: Math.min(60, Math.max(1, Math.round(result.duration / 1000))) } })
    }
    this._onRecordError = () => {
      clearInterval(this._recordTimer); this._startingRecord = false; this._holding = false
      this.setData({ recording: false, authorizing: false, cancelRecording: false })
      if (this._active) wx.showToast({ title: '录音未成功，请检查麦克风权限后重试', icon: 'none' })
    }
    this._onInterruption = () => { this._discardRecording = true; this._holding = false; this._recorder.stop() }
    this._recorder.onStart(this._onRecordStart)
    this._recorder.onStop(this._onRecordStop)
    this._recorder.onError(this._onRecordError)
    this._recorder.onInterruptionBegin(this._onInterruption)
  },
  startRecording(event) {
    if (this.data.pendingAudio || this.data.saving || this.data.audioSaving || this.data.recording || this.data.authorizing) return
    if (!wx.getRecorderManager) { wx.showToast({ title: '当前环境暂不支持录音，请使用文字输入', icon: 'none' }); return }
    this.stopPlayback()
    this._holding = true; this._discardRecording = false
    const request = this._recordRequest = (this._recordRequest || 0) + 1
    this._touchY = event.touches && event.touches[0] ? event.touches[0].clientY : 0
    this.setData({ authorizing: true, cancelRecording: false })
    wx.authorize({
      scope: 'scope.record',
      success: () => {
        if (request !== this._recordRequest) return
        if (!this._holding || !this._active) { this.setData({ authorizing: false }); return }
        try { this.setupRecorder(); this._startingRecord = true; this._recorder.start({ duration: 60000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: 'mp3' }) }
        catch (_) { this._startingRecord = false; this._holding = false; this.setData({ authorizing: false }); wx.showToast({ title: '录音暂不可用，请重试或使用文字', icon: 'none' }) }
      },
      fail: () => {
        if (request !== this._recordRequest) return
        this._holding = false; this.setData({ authorizing: false })
        if (!this._active) return
        wx.showModal({ title: '需要麦克风权限', content: '允许使用麦克风后，就可以按住说话。你也可以继续用文字聊天。', confirmText: '去设置', cancelText: '用文字', success: result => { if (result.confirm) wx.openSetting({}); else this.setData({ mode: 'text' }) } })
      }
    })
  },
  moveRecording(event) {
    if (!this._holding || !event.touches || !event.touches[0]) return
    this.setData({ cancelRecording: this._touchY - event.touches[0].clientY > 60 })
  },
  finishRecording() {
    this._holding = false
    this._recordRequest = (this._recordRequest || 0) + 1
    if (this._recorder && (this.data.recording || this._startingRecord)) this._recorder.stop()
    else this.setData({ authorizing: false })
  },
  cancelRecordGesture() { this._discardRecording = true; this.finishRecording() },
  discardAudio() {
    if (this.data.audioSaving) return
    this.stopPlayback(); this.setData({ pendingAudio: null })
  },
  sendAudio() {
    if (!this.data.pendingAudio || this.data.audioSaving || this.data.saving) return
    const pending = this.data.pendingAudio
    this.stopPlayback(); this.setData({ audioSaving: true })
    const save = filePath => {
      try {
        service.sendChatMessage({ audio: { filePath, duration: pending.duration } })
        this.setData({ pendingAudio: null }); this.refresh()
      } catch (error) {
        // Retain a moved file for retry when the record itself failed to persist.
        this.setData({ pendingAudio: { filePath, duration: pending.duration, persisted: true } }); showError(error)
      }
      this.setData({ audioSaving: false })
    }
    if (pending.persisted) { save(pending.filePath); return }
    wx.getFileSystemManager().saveFile({ tempFilePath: pending.filePath, success: result => save(result.savedFilePath), fail: () => { this.setData({ audioSaving: false }); wx.showToast({ title: '语音保存失败，录音仍在，请重试', icon: 'none' }) } })
  },
  playVoice(event) {
    const messageId = event.currentTarget.dataset.id
    const audio = messageId === 'pending' ? this.data.pendingAudio : (this.data.messages.find(message => message.id === messageId) || {}).audio
    if (!audio) return
    if (this.data.playingId === messageId) { this.stopPlayback(); return }
    this.stopPlayback()
    if (!this._audio) {
      this._audio = wx.createInnerAudioContext()
      this._audio.onEnded(() => this.setData({ playingId: '' }))
      this._audio.onError(() => { this.setData({ playingId: '' }); if (this._active) wx.showToast({ title: '这段语音暂时无法播放，请重试', icon: 'none' }) })
    }
    this._audio.src = audio.filePath; this.setData({ playingId: messageId }); this._audio.play()
  },
  stopPlayback() { if (this._audio) this._audio.stop(); this.setData({ playingId: '' }) }
})
