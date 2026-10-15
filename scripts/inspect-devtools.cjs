const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
;(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const page = await mini.currentPage()
    console.log('Current page:', page.path)
    console.log('Argument support:', await mini.evaluate(function (value) { return { echo: value } }, 'ok'))
    console.log('Viewport:', await mini.evaluate(() => wx.getWindowInfo()))
    console.log('Storage status:', await mini.evaluate(() => {
      try { const value = wx.getStorageSync('make_again_local_v1'); return { exists: !!value, type: typeof value } }
      catch (error) { return { error: error.message } }
    }))
    const title = await page.$('.heading')
    if (title) console.log('Heading:', await title.text())
    fs.mkdirSync(path.resolve(__dirname, '../artifacts'), { recursive: true })
    await mini.screenshot({ path: path.resolve(__dirname, '../artifacts/initial.png') })
    console.log('Simulator screenshot captured.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
