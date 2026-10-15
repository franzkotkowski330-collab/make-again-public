Page({ goBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/profile/profile' }) }) } })
