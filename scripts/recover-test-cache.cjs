const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const automator = require('miniprogram-automator')
;(async () => {
  const file = path.resolve(__dirname, '../artifacts/cache-backup.json')
  if (!fs.existsSync(file)) { console.log('No interrupted-test cache backup exists.'); return }
  const original = JSON.parse(fs.readFileSync(file, 'utf8'))
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const available = await mini.evaluate(function () { return { get: typeof wx.getStorageSync, set: typeof wx.setStorageSync, remove: typeof wx.removeStorageSync } })
    assert.deepEqual(available, { get: 'function', set: 'function', remove: 'function' })
    await mini.evaluate(function (backup) {
      if (backup === '' || backup === null) wx.removeStorageSync('make_again_local_v1')
      else wx.setStorageSync('make_again_local_v1', backup)
      return { restored: true }
    }, original)
    const restored = await mini.evaluate(function () { return { value: wx.getStorageSync('make_again_local_v1') } })
    assert.deepEqual(restored.value, original)
    fs.unlinkSync(file)
    console.log('Original app cache restored and verified; native storage APIs are available.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
