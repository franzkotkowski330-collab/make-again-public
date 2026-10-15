const service = require('../../utils/service')
const { PROFILE_FIELDS, LOSS_TYPES } = require('../../utils/constants')
const { showError } = require('../../utils/ui')
Page({
  data: { fields: [], lossTypes: LOSS_TYPES, lossIndex: 0, saving: false, error: '', ready: false },
  onLoad() {
    try {
      const profile = service.getProfile()
      const fields = PROFILE_FIELDS.map(field => Object.assign({}, field, profile[field.key]))
      this.setData({ fields, lossIndex: Math.max(0, LOSS_TYPES.findIndex(t => t.value === profile.lossType.value)), ready: true })
    } catch (error) { this.setData({ error: error.message }) }
  },
  changeType(event) {
    const index = Number(event.detail.value)
    this.setData({ lossIndex: index, 'fields[0].value': LOSS_TYPES[index].value }); this._dirty = true
  },
  onInput(event) {
    const index = Number(event.currentTarget.dataset.index)
    this.setData({ ['fields[' + index + '].value']: event.detail.value }); this._dirty = true
  },
  toggleDecline(event) {
    const index = Number(event.currentTarget.dataset.index)
    this.setData({ ['fields[' + index + '].declined']: event.detail.value }); this._dirty = true
  },
  save() {
    if (!this.data.ready || this.data.saving) return
    this.setData({ saving: true })
    const profile = {}
    this.data.fields.forEach(field => { profile[field.key] = { value: field.value, declined: field.declined } })
    try {
      service.saveProfile(profile); this._dirty = false
      wx.showToast({ title: '已保存到本机', icon: 'success' })
      wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/profile/profile' }) })
    } catch (error) { showError(error); this.setData({ saving: false }) }
  },
  cancel() {
    const back = () => wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/profile/profile' }) })
    if (!this._dirty) { back(); return }
    wx.showModal({ title: '放下这次修改？', content: '未保存的修改会丢弃，之前的记录会保留。', confirmText: '放下修改', cancelText: '继续填写', success: result => { if (result.confirm) back() } })
  }
})
