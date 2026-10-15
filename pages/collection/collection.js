const service = require('../../utils/service')
const { COLLECTION_TABS } = require('../../utils/constants')
Page({
  data: { tabs: COLLECTION_TABS, active: 'letters', items: [], error: '', emptyTitle: '', emptyDescription: '', letterArriving: false, letterPlaced: false, arrivalAnimation: {} },
  onShow() {
    this._active = true
    const pending = getApp().globalData.pendingLetterArrival
    if (pending) {
      getApp().globalData.pendingLetterArrival = false
      this.setData({ active: 'letters', letterArriving: true, letterPlaced: false, arrivalAnimation: {} })
      wx.pageScrollTo({ scrollTop: 0, duration: 0 })
    }
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
    this.setData({ letterArriving: false, letterPlaced: true })
    this._placedTimer = setTimeout(() => this.setData({ letterPlaced: false }), 2000)
  },
  selectTab(event) { this.setData({ active: event.detail.value || event.currentTarget.dataset.key }); this.refresh() },
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
  readLetter() { wx.navigateTo({ url: '/pages/letter/letter' }) }
})
