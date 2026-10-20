const service = require('../../utils/service')
const { PROFILE_FIELDS, LOSS_TYPES } = require('../../utils/constants')
Page({
  data: { hasGuest: false, fields: [], error: '', loginNotice: false },
  onShow() {
    try {
      const overview = service.getOverview()
      const fields = overview.hasGuest ? PROFILE_FIELDS.map(field => {
        const entry = overview.journey.profile[field.key]
        let display = entry.declined ? '暂不分享' : entry.value || (entry.voiceMessageId ? '已通过语音回答，可回聊天中收听' : '还没有填写')
        if (field.key === 'lossType' && entry.value && !entry.declined) display = LOSS_TYPES.find(t => t.value === entry.value).label
        return { key: field.key, label: field.label, display, filled: !!entry.value || entry.declined || !!entry.voiceMessageId }
      }) : []
      this.setData(Object.assign(overview, { fields, nickname: overview.hasGuest ? overview.journey.profile.nickname.value : '', error: '' }))
    } catch (error) { this.setData({ error: error.message }) }
  },
  editProfile() { wx.navigateTo({ url: '/pages/profile/edit' }) },
  startJourney() { wx.switchTab({ url: '/pages/chat/chat' }) },
  readPrivacy() { wx.navigateTo({ url: '/pages/privacy/privacy' }) },
  openLogin() { this.setData({ loginNotice: true }) },
  closeLoginNotice() { this.setData({ loginNotice: false }) },
  readLetter() { wx.navigateTo({ url: '/pages/letter/letter' }) }
})
