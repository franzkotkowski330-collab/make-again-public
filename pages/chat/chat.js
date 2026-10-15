const service = require('../../utils/service')
const { showError, formatTime } = require('../../utils/ui')
Page({
  data: { hasGuest: false, messages: [], draft: '', canSend: false, saving: false, scrollTarget: '', chatHeight: 600, keyboardHeight: 0, error: '' },
  onShow() {
    this._active = true
    getApp().globalData.pendingChatGuide = false
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this._viewportHeight = info.windowHeight
    this.setData({ chatHeight: info.windowHeight, keyboardHeight: 0 })
    this.refresh()
  },
  onHide() { this._active = false },
  refresh() {
    try {
      const overview = service.getOverview()
      const messages = service.listMessages().map(message => Object.assign({}, message, { timeLabel: formatTime(message.createdAt) }))
      this.setData({ hasGuest: overview.hasGuest, messages, error: '', scrollTarget: messages.length ? 'msg-' + messages[messages.length - 1].id : '' })
    } catch (error) { this.setData({ error: error.message }) }
  },
  acceptConsent() {
    if (this.data.saving) return
    this.setData({ saving: true })
    try { service.acceptConsent(); this.refresh() }
    catch (error) { showError(error) }
    this.setData({ saving: false })
  },
  declineConsent() { wx.switchTab({ url: '/pages/home/home' }) },
  readPrivacy() { wx.navigateTo({ url: '/pages/privacy/privacy' }) },
  onInput(event) { const draft = event.detail.value; this.setData({ draft, canSend: !!draft.trim() }) },
  keyboardChange(event) {
    const height = event.detail.height || 0
    this.setData({ keyboardHeight: height, chatHeight: Math.max(180, this._viewportHeight - height) })
  },
  blurInput() { wx.hideKeyboard() },
  send() {
    if (this.data.saving || !this.data.canSend) return
    this.setData({ saving: true })
    try {
      service.saveMessage(this.data.draft)
      this.setData({ draft: '', canSend: false })
      this.refresh()
    } catch (error) { showError(error) }
    this.setData({ saving: false })
  }
})
