const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
;(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const page = await mini.currentPage()
    const result = await mini.evaluate(function () {
      const key = 'make_again_storage_probe'
      if (typeof wx.setStorageSync !== 'function') throw new Error('Native storage API is unavailable')
      const before = wx.getStorageSync('make_again_local_v1')
      try {
        wx.setStorageSync(key, { ok: true })
        if (!wx.getStorageSync(key).ok) throw new Error('Native cache write did not persist')
      } finally { wx.removeStorageSync(key) }
      return { nativeStorageRoundTrip: true, userCacheUnchanged: JSON.stringify(before) === JSON.stringify(wx.getStorageSync('make_again_local_v1')), consentPresent: !!(before && before.consent) }
    })
    assert.equal(result.nativeStorageRoundTrip, true)
    assert.equal(result.userCacheUnchanged, true)
    const view = await page.$(page.path === 'pages/chat/chat' ? '.chat-screen' : '.screen')
    assert.ok(view)
    const size = await view.size()
    assert.ok(Number(size.width) > 0 && Number(size.height) > 0)
    fs.writeFileSync(path.resolve(__dirname, '../artifacts/final-smoke.json'), JSON.stringify(Object.assign({ page: page.path, rendered: true }, result), null, 2))
    console.log('PASS: current simulator page renders; native storage read/write/remove works; user cache is unchanged.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
