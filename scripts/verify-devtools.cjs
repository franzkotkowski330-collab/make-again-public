// Exercise real WeChat simulator pages; preserve and restore the user's existing app cache.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
const KEY = 'make_again_local_v1'
const artifacts = path.resolve(__dirname, '../artifacts')
const results = [], exceptions = []
const screenshotFailures = []
const pass = name => { results.push(name); console.log('PASS:', name) }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitForRoute(mini, expected) {
  for (let index = 0; index < 20; index++) {
    const page = await mini.currentPage()
    if (page.path === expected) return page
    await delay(300)
  }
  throw new Error('Navigation did not reach ' + expected + '; current: ' + (await mini.currentPage()).path)
}
;(async () => {
  fs.mkdirSync(artifacts, { recursive: true })
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  let storageMockActive = false
  console.log('Connected to WeChat DevTools.')
  if (process.env.TRACE_DEVTOOLS === '1') {
    const send = mini.connection.send.bind(mini.connection)
    mini.connection.send = async (method, params) => { console.log('BEGIN', method); const result = await send(method, params); console.log('END', method); return result }
  }
  const readState = async () => (await mini.evaluate(function () { return { value: wx.getStorageSync('make_again_local_v1') } })).value
  const relaunch = async url => {
    // Do not resend a navigation when the old SDK loses its callback during a redirect.
    const requested = mini.reLaunch(url).catch(error => ({ error: error.message }))
    await Promise.race([requested, delay(4500)])
    return mini.currentPage()
  }
  const capture = async name => {
    // Capture in separate sessions: this DevTools release can stall navigation after capture.
    if (process.env.SKIP_SCREENSHOTS !== '0') return
    await delay(600)
    try { await mini.screenshot({ path: path.join(artifacts, name) }) }
    catch (error) { screenshotFailures.push(name); console.log('Screenshot unavailable:', name) }
  }
  let original
  try { original = await readState() }
  catch (error) { mini.disconnect(); throw error }
  // Recovery copy in ignored artifacts, never print its private contents.
  fs.writeFileSync(path.join(artifacts, 'cache-backup.json'), JSON.stringify(original))
  console.log('Cache snapshot ready.')
  mini.on('exception', event => exceptions.push(event))
  try {
    await mini.evaluate(function () { wx.removeStorageSync('make_again_local_v1'); return { ok: true } })
    console.log('Test cache cleared.')
    await relaunch('/pages/home/home')
    console.log('Home reloaded.')
    let page = await waitForRoute(mini, 'pages/letter/letter')
    assert.equal(page.path, 'pages/letter/letter')
    assert.equal(await page.data('first'), true)
    await capture('01-first-letter.png')
    pass('新缓存首次进入自动展示作者信')
    await mini.pageScrollTo(99999)
    await delay(500)
    await (await page.$('.primary')).tap()
    page = await waitForRoute(mini, 'pages/chat/chat')
    assert.equal(page.path, 'pages/chat/chat')
    assert.equal(await page.data('hasGuest'), false)
    let state = await readState()
    assert.equal(state.letterRead, true); assert.equal(state.guest, null)
    await capture('02-consent.png')
    await (await page.$('.decline-button')).tap()
    await delay(600)
    state = await readState()
    assert.equal(state.guest, null); assert.deepEqual(state.messages, [])
    pass('拒绝说明不建立游客、不保存倾诉')
    await relaunch('/pages/home/home')
    page = await mini.currentPage()
    assert.equal(page.path, 'pages/home/home')
    pass('已读作者信后重新进入不再自动弹出')
    await capture('03-home-before-consent.png')
    page = await mini.switchTab('/pages/chat/chat')
    const consentScroll = await page.$('.consent-scroll')
    await consentScroll.scrollTo(0, 2000)
    await (await page.$('.primary')).tap()
    await delay(600)
    assert.equal(await page.data('hasGuest'), true)
    state = await readState()
    assert.equal(state.journeys.length, 1)
    assert.ok(state.consent.acceptedAt)
    pass('点击同意后创建本地游客与当前旅程')
    const input = await page.$('.chat-input')
    await input.trigger('keyboardheightchange', { height: 280 })
    await delay(250)
    const chatBox = await page.$('.chat-screen'), dock = await page.$('.input-dock')
    const chatOffset = await chatBox.offset(), dockOffset = await dock.offset()
    const chatSize = await chatBox.size(), dockSize = await dock.size()
    assert.ok(Number(dockOffset.top) + Number(dockSize.height) <= Number(chatOffset.top) + Number(chatSize.height) + 1)
    await input.trigger('keyboardheightchange', { height: 0 }); await delay(150)
    pass('模拟键盘高度事件后输入区保持在页面可见范围（不代表真机验收）')
    await input.input('【验收示例】今天很想念外公。\n我想慢慢写下这些回忆。')
    await (await page.$('.send-button')).tap()
    await delay(500)
    assert.equal((await page.data('messages')).length, 1)
    assert.equal(await page.data('draft'), '')
    state = await readState()
    assert.equal(state.messages.length, 1); assert.equal(state.messages[0].role, 'user')
    assert.equal(state.messages[0].journeyId, state.currentJourneyId)
    await capture('04-saved-chat.png')
    pass('原生输入框保存多行文字，消息关联旅程且无伪造 AI 回复')
    // Inject a write failure through the official test SDK, then remove the mock.
    await mini.mockWxMethod('setStorageSync', function () { throw new Error('simulated quota failure') })
    storageMockActive = true
    try {
      await input.input('【验收示例】这次保存应当失败，草稿应保留。')
      await (await page.$('.send-button')).tap()
      await delay(300)
      assert.equal(await page.data('draft'), '【验收示例】这次保存应当失败，草稿应保留。')
      assert.equal((await page.data('messages')).length, 1)
    } finally { await mini.restoreWxMethod('setStorageSync'); storageMockActive = false }
    pass('保存失败保留输入草稿且不增加已保存消息')
    await relaunch('/pages/chat/chat')
    page = await mini.currentPage()
    assert.equal((await page.data('messages')).length, 1)
    pass('重新启动页面仍恢复已保存聊天历史')
    page = await mini.switchTab('/pages/profile/profile')
    await capture('05-profile-before-edit.png')
    await (await page.$('.edit-profile')).tap()
    await delay(500)
    page = await mini.currentPage()
    assert.equal(page.path, 'pages/profile/edit')
    const picker = await page.$('picker')
    await picker.trigger('change', { value: '2' })
    await (await page.$('.form-input')).input('外公（验收示例）')
    const textareas = await page.$$('.form-textarea')
    await textareas[0].input('【验收示例】晚上容易想起从前。')
    await textareas[1].input('【验收示例】现在只想被倾听。')
    await mini.pageScrollTo(99999)
    await capture('06-edit-profile.png')
    await (await page.$('.primary')).tap()
    await delay(600)
    page = await mini.currentPage()
    assert.equal(page.path, 'pages/profile/profile')
    assert.equal(await page.data('profileCount'), 4)
    state = await readState()
    const profile = state.journeys[0].profile
    assert.equal(profile.lossType.value, 'relative'); assert.equal(profile.relationship.value, '外公（验收示例）')
    await capture('07-profile-saved.png')
    pass('原生 picker、input、textarea 保存四类建档，并返回原页面')
    await (await page.$('.edit-profile')).tap(); await delay(400)
    page = await mini.currentPage()
    const switches = await page.$$('switch')
    await switches[1].tap()
    await mini.pageScrollTo(99999)
    await (await page.$('.primary')).tap(); await delay(500)
    state = await readState()
    assert.equal(state.journeys[0].profile.relationship.declined, true)
    assert.equal(state.journeys[0].profile.relationship.value, '')
    pass('暂不分享开关保存后清除已填内容')
    page = await mini.switchTab('/pages/home/home')
    assert.equal(await page.data('messageCount'), 1)
    assert.equal(await page.data('profileCount'), 4)
    await capture('08-home-with-journey.png')
    pass('首页同步旅程建档状态及实际消息数量')
    page = await mini.switchTab('/pages/collection/collection')
    await capture('09-collection-letters.png')
    for (const key of ['items', 'photos', 'memories', 'letters']) {
      await (await page.$('[data-key="' + key + '"]')).tap()
      await delay(300)
      assert.equal(await page.data('active'), key)
    }
    await (await page.$('.letter-card')).tap(); await delay(500)
    page = await mini.currentPage(); assert.equal(page.path, 'pages/letter/letter')
    assert.equal(await page.data('first'), false)
    await mini.pageScrollTo(99999)
    await (await page.$('.secondary')).tap(); await delay(400)
    assert.equal((await mini.currentPage()).path, 'pages/collection/collection')
    pass('收藏四栏目可切换，作者信可重读并返回收藏')
    page = await mini.switchTab('/pages/profile/profile')
    await (await page.$('.settings-row')).tap(); await delay(600)
    assert.equal((await mini.currentPage()).path, 'pages/privacy/privacy')
    await mini.navigateBack()
    assert.equal((await mini.currentPage()).path, 'pages/profile/profile')
    pass('隐私说明正常打开并返回原位置')
    assert.equal(exceptions.length, 0, 'Unexpected simulator runtime exceptions')
    pass('所有已测试页面无运行时异常')
    fs.writeFileSync(path.join(artifacts, 'devtools-results.json'), JSON.stringify({ passed: results, runtimeExceptions: exceptions.length, environment: 'WeChat DevTools simulator; real device not tested', screenshotsSkipped: process.env.SKIP_SCREENSHOTS !== '0', screenshotsContainTestData: true, screenshotFailures }, null, 2))
    console.log('WeChat DevTools verification complete:', results.length, 'checks')
  } finally {
    if (storageMockActive) await mini.restoreWxMethod('setStorageSync').catch(() => {})
    await mini.evaluate(function (key, backup) { if (backup === '' || backup === undefined || backup === null) wx.removeStorageSync(key); else wx.setStorageSync(key, backup); return { ok: true } }, KEY, original)
    fs.unlinkSync(path.join(artifacts, 'cache-backup.json'))
    await relaunch('/pages/home/home')
    mini.disconnect()
    console.log('Original local app data restored; simulator left open.')
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
