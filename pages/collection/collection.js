const service = require('../../utils/service')
const { COLLECTION_TABS } = require('../../utils/constants')
const { showError } = require('../../utils/ui')
Page({
  data: { tabs: COLLECTION_TABS, active: 'letters', items: [], error: '', emptyTitle: '', emptyDescription: '', letterArriving: false, letterPlaced: false, arrivalAnimation: {}, showChatGuide: false, openingChat: false },
  onShow() {
    this._active = true
    const globalData = getApp().globalData
    const pending = globalData.pendingLetterArrival
    if (pending) {
      globalData.pendingLetterArrival = false
      globalData.pendingChatGuide = true
      this.setData({ active: 'letters', letterArriving: true, letterPlaced: false, arrivalAnimation: {} })
      wx.pageScrollTo({ scrollTop: 0, duration: 0 })
    }
    this.setData({ showChatGuide: !!globalData.pendingChatGuide })
    this.syncGuideTabBar()
    this.refresh()
    if (pending) wx.nextTick(() => this.placeLetter())
  },
  onHide() { this.stopArrival() },
  onUnload() { this.stopArrival() },
  stopArrival() {
    this._active = false
    clearTimeout(this._arrivalTimer)
    clearTimeout(this._placedTimer)
    this.setData({ letterArriving: false, letterPlaced: false })
    this.syncGuideTabBar()
  },
  placeLetter() {
    if (!this._active) return
    wx.createSelectorQuery().in(this).select('.letter-list').boundingClientRect().select('.arriving-paper').boundingClientRect().exec(rects => {
      if (!this._active) return
      const target = rects && rects[0], paper = rects && rects[1]
      if (!target || !paper || !paper.width || !paper.height) { this.finishArrival(); return }
      const animation = wx.createAnimation({ duration: 900, timingFunction: 'cubic-bezier(.22,.7,.26,1)' })
      animation.translate(target.left + target.width / 2 - paper.left - paper.width / 2, target.top + target.height / 2 - paper.top - paper.height / 2).scale(target.width / paper.width, target.height / paper.height).opacity(.2).step()
      this.setData({ arrivalAnimation: animation.export() })
      this._arrivalTimer = setTimeout(() => this.finishArrival(), 920)
    })
  },
  finishArrival() {
    if (!this._active) return
    this.setData({ letterArriving: false, letterPlaced: true })
    this.syncGuideTabBar()
    this._placedTimer = setTimeout(() => this.setData({ letterPlaced: false }), 2000)
  },
  blockGuideTouch() {},
  syncGuideTabBar() {
    const hidden = !!(this._active && this.data.showChatGuide && !this.data.letterArriving && this.data.active === 'letters')
    if (hidden === !!this._guideTabBarHidden) return
    this._guideTabBarHidden = hidden
    // The spotlight supplies its own navigation while the native tab bar is hidden.
    const method = hidden ? 'hideTabBar' : 'showTabBar'
    if (typeof wx[method] === 'function') wx[method]({ animation: false })
  },
  selectTab(event) { this.setData({ active: event.detail.value || event.currentTarget.dataset.key }); this.syncGuideTabBar(); this.refresh() },
  refresh() {
    const content = {
      items: ['一些物品，装着一段故事', '物品上传与保存将在后续阶段接入。'],
      photos: ['把想留住的瞬间，慢慢收藏', '照片上传与关联故事将在后续阶段接入。'],
      memories: ['不必急着把记忆整理好', 'AI 记忆提取与编辑将在后续阶段接入。'],
      letters: ['等待下一封信', '旅程报告与仪式信件将在后续阶段接入。']
    }
    try {
      const empty = content[this.data.active]
      this.setData({ items: service.listCollection(this.data.active), emptyTitle: empty[0], emptyDescription: empty[1], error: '' })
    } catch (error) { this.setData({ items: [], error: error.message }) }
  },
  readLetter() { wx.navigateTo({ url: '/pages/letter/letter' }) },
  startChat() {
    if (this.data.openingChat) return
    this.setData({ openingChat: true })
    wx.switchTab({
      url: '/pages/chat/chat',
      success: () => { getApp().globalData.pendingChatGuide = false; this.setData({ showChatGuide: false }); this.syncGuideTabBar() },
      fail: () => showError(new Error('暂时无法打开聊天，请再试一次。')),
      complete: () => this.setData({ openingChat: false })
    })
  }
})
