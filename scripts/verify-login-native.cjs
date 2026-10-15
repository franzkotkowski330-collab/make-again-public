// Check only the native login page. Do not write or clear the user's app cache.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const output = path.resolve(__dirname, '../artifacts/ui/login-native')
let mini
const watchdog = setTimeout(() => { console.error('Native login verification timed out'); if (mini) mini.disconnect(); process.exit(1) }, 50000)

;(async () => {
  fs.mkdirSync(output, { recursive: true })
  mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  const exceptions = []
  mini.on('exception', event => exceptions.push(event))
  const readCache = () => mini.evaluate(() => wx.getStorageSync('make_again_local_v1'))
  const original = await readCache()
  let mocked = false
  try {
    const page = await mini.reLaunch('/pages/login/login')
    assert.equal(page.path, 'pages/login/login')
    await wait(2300)
    assert.equal((await mini.currentPage()).path, 'pages/login/login')
    assert.equal((await page.$$('button')).length, 1)
    const button = await page.$('.wechat-login')
    assert.equal(await button.text(), '微信登录')
    const viewport = await mini.evaluate(() => wx.getWindowInfo())
    const words = []
    for (const word of await page.$$('.brand-word')) {
      const offset = await word.offset(), size = await word.size()
      const font = await word.style('font-family')
      const paddingLeft = await word.style('padding-left')
      assert.match(font, /MAParisienne/)
      assert.ok(Number(offset.left) >= 0)
      assert.ok(Number(offset.left) + Number(size.width) <= viewport.windowWidth)
      assert.ok(parseFloat(paddingLeft) > 0)
      words.push({ text: await word.text(), left: offset.left, width: size.width, paddingLeft, font })
    }
    const buttonOffset = await button.offset(), buttonSize = await button.size()
    assert.ok(Number(buttonOffset.top) + Number(buttonSize.height) <= viewport.windowHeight)
    console.log('PASS: native page remains visible, has one bottom button and displays both brand words within the viewport.')

    await mini.mockWxMethod('login', { code: '' })
    mocked = true
    await button.tap()
    await wait(250)
    assert.equal(await page.data('isLoggingIn'), false)
    assert.ok(await page.data('loginError'))
    assert.equal((await mini.currentPage()).path, 'pages/login/login')
    await mini.restoreWxMethod('login')
    mocked = false
    console.log('PASS: missing login credentials keep the page readable and permit retry.')

    await button.tap()
    await wait(400)
    let route
    for (let index = 0; index < 45; index++) {
      route = (await mini.currentPage()).path
      if (route === 'pages/letter/letter') break
      await wait(250)
    }
    if (route !== 'pages/letter/letter') throw new Error('Real wx.login did not lead to the letter page: ' + route)
    await wait(300)
    const letterPage = await mini.currentPage()
    const letter = await letterPage.$('#author-letter')
    assert.equal(await letter.data('first'), true, 'Login must open the first-run letter page')
    assert.equal(await letter.data('opening'), false, 'Envelope must stay closed after login')
    assert.ok(await letter.$('.ml-intro'), 'Tap-to-open hint must be visible')
    await wait(1600)
    assert.equal(await letter.data('opening'), false, 'Envelope must not open by itself')
    const envelope = await letter.$('.ml-envelope')
    await envelope.tap()
    await wait(300)
    const letterAfterTap = await letterPage.$('#author-letter')
    assert.equal(await letterAfterTap.data('opening'), true, 'Tapping the envelope must start the opening animation')
    await wait(6800)
    const letterAfterReading = await letterPage.$('#author-letter')
    assert.equal(await letterAfterReading.data('reading'), true)
    assert.deepEqual(await readCache(), original)
    console.log('PASS: real wx.login leads to the letter page; the envelope stays closed until tapped; existing local cache unchanged. No account binding is claimed.')
    await mini.reLaunch('/pages/login/login')
    assert.equal(exceptions.length, 0, JSON.stringify(exceptions))
    fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify({
      loginRequiresClick: true, buttonCount: 1, words, buttonOffset, buttonSize,
      windowWidth: viewport.windowWidth, windowHeight: viewport.windowHeight,
      missingCodeRetry: true, loginEntry: 'pages/letter/letter', envelopeRequiresTap: true, accountBackendConnected: false,
      existingCacheUnchanged: true, runtimeExceptions: exceptions.length, realDeviceTested: false
    }, null, 2))
    // This IDE's screenshot endpoint can fail independently of the app. Keep it opt-in.
    if (process.env.CAPTURE_LOGIN === '1') await mini.screenshot({ path: path.join(output, 'login.png') })
    console.log('Native login verification saved:', output)
  } finally {
    if (mocked) await mini.restoreWxMethod('login')
    mini.disconnect()
  }
})().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => clearTimeout(watchdog))
