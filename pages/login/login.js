Page({
  data: { animate: false, isLoggingIn: false, loginError: '' },
  onShow() {
    this._active = true
    this._loginAttempt = (this._loginAttempt || 0) + 1
    this.setData({ animate: false, isLoggingIn: false, loginError: '' })
    wx.nextTick(() => {
      if (!this._active) return
      this.setData({ animate: true })
    })
  },
  onHide() { this._active = false; this._loginAttempt++ },
  onUnload() { this._active = false; this._loginAttempt++ },
  loginWithWechat() {
    if (!this._active || this.data.isLoggingIn) return
    const attempt = ++this._loginAttempt
    this.setData({ isLoggingIn: true, loginError: '' })
    const current = () => this._active && attempt === this._loginAttempt
    const fail = message => {
      if (current()) this.setData({ isLoggingIn: false, loginError: message })
    }
    wx.login({
      timeout: 10000,
      success: result => {
        if (!current()) return
        if (!result.code) { fail('微信登录暂时未能完成，请重试。'); return }
        // The current app is local-only. A code is not a verified account session;
        // account binding still requires a server-side code2Session integration.
        // Always show the envelope after login, even when the letter was read on this device before.
        wx.redirectTo({
          url: '/pages/letter/letter?first=1',
          fail: () => wx.switchTab({
            url: '/pages/home/home',
            fail: () => fail('暂时无法进入，请重试。')
          })
        })
      },
      fail: () => fail('微信登录暂时未能完成，请重试。')
    })
  }
})
