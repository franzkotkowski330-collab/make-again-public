const service = require('../../utils/service')
const { showError } = require('../../utils/ui')
Page({
  data: { first: false, saving: false },
  onLoad(options) { this.setData({ first: options.first === '1' }) },
  continueJourney() {
    if (this.data.saving) return
    try {
      service.markLetterRead()
      this.setData({ saving: true })
      this.selectComponent('#author-letter').archive()
    } catch (error) { this.setData({ saving: false }); showError(error) }
  },
  cancelArchive() { this.setData({ saving: false }) },
  archiveLetter() {
    getApp().globalData.pendingLetterArrival = true
    wx.switchTab({ url: '/pages/collection/collection', fail: () => {
      getApp().globalData.pendingLetterArrival = false
      this.setData({ saving: false })
      this.selectComponent('#author-letter').resetClosing()
      showError(new Error('暂时无法打开收藏，请点击“开始”重试。'))
    } })
  },
  goBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/collection/collection' }) }) }
})
