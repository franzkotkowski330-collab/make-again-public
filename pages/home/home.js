const service = require('../../utils/service')
const { showError } = require('../../utils/ui')
Page({
  data: { hasGuest: false, messageCount: 0, profileCount: 0, loadError: '', showFirstLetter: false, savingLetter: false },
  onHide() { wx.showTabBar({ animation: false }) },
  onUnload() { wx.showTabBar({ animation: false }) },
  onShow() {
    try {
      const overview = service.getOverview()
      this.setData(Object.assign(overview, { loadError: '', showFirstLetter: this.data.showFirstLetter || !overview.letterRead }))
      if (this.data.showFirstLetter) wx.hideTabBar({ animation: false })
    }
    catch (error) { this.setData({ loadError: error.message }) }
  },
  beginLetter() {
    if (this.data.savingLetter) return
    try {
      service.markLetterRead()
      this.setData({ savingLetter: true })
      this.selectComponent('#first-letter').archive()
    } catch (error) { showError(error) }
  },
  cancelLetter() { this.setData({ savingLetter: false }) },
  archiveLetter() {
    getApp().globalData.pendingLetterArrival = true
    wx.showTabBar({ animation: false })
    wx.switchTab({ url: '/pages/collection/collection', success: () => this.setData({ showFirstLetter: false, savingLetter: false }), fail: () => {
      getApp().globalData.pendingLetterArrival = false
      this.setData({ savingLetter: false })
      this.selectComponent('#first-letter').resetClosing()
      wx.hideTabBar({ animation: false })
      showError(new Error('暂时无法打开收藏，请点击“开始”重试。'))
    } })
  },
  startChat() { wx.switchTab({ url: '/pages/chat/chat' }) },
  editProfile() {
    if (!this.data.hasGuest) { this.startChat(); return }
    wx.navigateTo({ url: '/pages/profile/edit' })
  },
  readLetter() { wx.navigateTo({ url: '/pages/letter/letter' }) },
  showUpcoming(event) { wx.showToast({ title: event.currentTarget.dataset.title + '将在后续阶段接入', icon: 'none', duration: 2500 }) },
  retry() { this.onShow(); if (this.data.loadError) showError(new Error(this.data.loadError)) }
})
