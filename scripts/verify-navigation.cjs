const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
;(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  const checks = []
  try {
    for (const [tab, route] of [['首页', 'home/home'], ['聊天', 'chat/chat'], ['收藏', 'collection/collection'], ['我的', 'profile/profile']]) {
      await mini.switchTab('/pages/' + route)
      await pause(600)
      const page = await mini.currentPage()
      assert.equal(page.path, 'pages/' + route)
      checks.push('原生底栏：' + tab)
      console.log('PASS:', checks[checks.length - 1])
    }
    let page = await mini.switchTab('/pages/collection/collection')
    for (const key of ['items', 'photos', 'memories', 'letters']) {
      await (await page.$('[data-key="' + key + '"]')).tap()
      await pause(500)
      assert.equal(await page.data('active'), key)
      console.log('PASS: 收藏栏目', key)
    }
    checks.push('收藏四栏目切换')
    await (await page.$('.letter-card')).tap(); await pause(700)
    page = await mini.currentPage(); assert.equal(page.path, 'pages/letter/letter')
    assert.equal(await page.data('first'), false)
    // Standard back navigation returns to the originating collection tab.
    await mini.navigateBack()
    assert.equal((await mini.currentPage()).path, 'pages/collection/collection')
    checks.push('作者信重读及返回收藏'); console.log('PASS:', checks[checks.length - 1])
    page = await mini.switchTab('/pages/profile/profile')
    await (await page.$('.settings-row')).tap(); await pause(700)
    assert.equal((await mini.currentPage()).path, 'pages/privacy/privacy')
    await mini.navigateBack()
    assert.equal((await mini.currentPage()).path, 'pages/profile/profile')
    checks.push('隐私说明及返回我的'); console.log('PASS:', checks[checks.length - 1])
    fs.writeFileSync(path.resolve(__dirname, '../artifacts/navigation-results.json'), JSON.stringify({ passed: checks, environment: 'WeChat DevTools simulator', changedUserContent: false }, null, 2))
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
