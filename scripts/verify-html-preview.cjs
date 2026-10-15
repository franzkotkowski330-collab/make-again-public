const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { pathToFileURL } = require('node:url')
const { spawn } = require('node:child_process')
const assert = require('node:assert/strict')
const WebSocket = require('ws')
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const browserPath = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'make-again-preview-browser-'))
const output = path.resolve(__dirname, '../artifacts/ui/login-preview')
fs.mkdirSync(output, { recursive: true })
const browser = spawn(browserPath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=9427', '--remote-allow-origins=http://localhost', '--user-data-dir=' + profile, '--window-size=1000,1000', 'about:blank'], { windowsHide: true, stdio: 'ignore' })
let socket, sequence = 0
const requests = new Map(), exceptions = []
async function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence; requests.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  })
}
async function evaluate(expression) {
  const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
async function screenshot(file) {
  const result = await cdp('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(path.join(output, file), Buffer.from(result.data, 'base64'))
}
async function checkSelectedTab(page) {
  const spotlight = await evaluate("document.getElementById('phone').classList.contains('ma-chat-onboarding')")
  const tabs = await evaluate(`Array.from(document.querySelectorAll('#tabbar [data-page]')).map(button => ({
    page: button.dataset.page, active: button.classList.contains('active'), current: button.getAttribute('aria-current'),
    color: getComputedStyle(button).color, selectedIconVisible: button.querySelector('.ma-tab-selected').offsetParent !== null
  }))`)
  assert.deepEqual(tabs.filter(tab => tab.active).map(tab => tab.page), [page], 'Only the current page is highlighted')
  assert.deepEqual(tabs.filter(tab => tab.current === 'page').map(tab => tab.page), [page])
  assert.deepEqual(tabs.filter(tab => tab.selectedIconVisible).map(tab => tab.page), [page], 'Only the current page shows the highlighted icon')
  assert.equal(tabs.find(tab => tab.active).color, 'rgb(102, 82, 115)')
  for (const tab of tabs.filter(tab => !tab.active)) assert.equal(tab.color, spotlight && tab.page === 'chat' ? 'rgb(113, 94, 128)' : 'rgb(158, 152, 166)')
}
;(async () => {
  let tabs
  for (let attempt = 0; attempt < 40; attempt++) {
    try { tabs = await (await fetch('http://127.0.0.1:9427/json/list')).json(); break } catch (_) { await wait(150) }
  }
  if (!tabs) throw new Error('Preview browser did not start')
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject) })
  socket.on('message', raw => {
    const message = JSON.parse(raw)
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params)
    const request = requests.get(message.id)
    if (request) { requests.delete(message.id); message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result) }
  })
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('DOM.enable'); await cdp('CSS.enable')
  await cdp('Page.navigate', { url: pathToFileURL(path.resolve(__dirname, '../Make-Again-登录页预览.html')).href })
  await evaluate('document.fonts.ready.then(() => true)')
  await wait(2400)
  assert.equal(await evaluate("document.querySelectorAll('[id=letterTemplate]').length"), 1)
  assert.equal(await evaluate("document.getElementById('splash').hidden"), false, 'Animation must not auto-navigate')
  assert.equal(await evaluate("document.querySelectorAll('#splash button').length"), 1)
  assert.equal(await evaluate("document.getElementById('loginWechatButton').textContent"), '微信登录')
  assert.equal(await evaluate("document.getElementById('letterMount').hidden"), true)
  await screenshot('01-login-desktop.png')
  const layouts = []
  for (const width of [320, 360, 390, 430]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height: 844, deviceScaleFactor: 1, mobile: true })
    await wait(80)
    const layout = await evaluate(`(() => {
      const phone = document.getElementById('phone').getBoundingClientRect();
      const button = document.getElementById('loginWechatButton').getBoundingClientRect();
      const canvas = document.createElement('canvas'), context = canvas.getContext('2d');
      const words = Array.from(document.querySelectorAll('#splash .brand-word')).map(node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        context.font = style.fontWeight + ' ' + style.fontSize + ' ' + style.fontFamily;
        const ink = context.measureText(node.textContent), start = box.left + parseFloat(style.paddingLeft);
        return { text: node.textContent, font: style.fontFamily, left: box.left, right: box.right,
          inkLeft: start - ink.actualBoundingBoxLeft, inkRight: start + ink.actualBoundingBoxRight };
      });
      return { width: innerWidth, phone: { left: phone.left, right: phone.right, bottom: phone.bottom },
        button: { left: button.left, right: button.right, bottom: button.bottom }, words,
        noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth };
    })()`)
    assert.ok(layout.noHorizontalOverflow, 'Horizontal overflow at ' + width)
    assert.ok(layout.button.bottom <= layout.phone.bottom - 10, 'Bottom button outside safe area')
    for (const word of layout.words) {
      assert.match(word.font, /MAParisienne/)
      assert.ok(word.left >= layout.phone.left && word.right <= layout.phone.right, JSON.stringify(layout))
      assert.ok(word.inkLeft >= word.left && word.inkRight <= word.right, 'Glimmer clips the flourish: ' + JSON.stringify(word))
    }
    layouts.push(layout)
    await screenshot('02-login-' + width + '.png')
  }
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
  await evaluate("document.getElementById('replay').click(); true")
  await wait(300)
  assert.equal(await evaluate("document.getElementById('splash').hidden"), false, 'Reduced motion must still wait for login')
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] })
  await evaluate("document.getElementById('loginWechatButton').click(); true")
  assert.equal(await evaluate("document.getElementById('loginWechatButton').disabled"), true)
  await wait(700)
  assert.equal(await evaluate("document.getElementById('splash').hidden"), true)
  assert.equal(await evaluate("document.getElementById('letterOverlay').classList.contains('ml-open')"), false, 'Envelope must wait for a tap after login')
  await wait(2000)
  assert.equal(await evaluate("document.getElementById('letterOverlay').classList.contains('ml-open')"), false, 'Envelope must stay closed until tapped')
  assert.equal(await evaluate("document.getElementById('letterPaper').hidden"), true, 'Letter must not appear before the tap')
  assert.equal(await evaluate("document.querySelector('#letterMount .ml-intro').offsetParent !== null"), true, 'Tap-to-open hint must be visible')
  await screenshot('03-light-envelope.png')
  await evaluate("document.querySelector('#letterMount .ml-envelope').click(); true")
  assert.equal(await evaluate("document.getElementById('letterOverlay').classList.contains('ml-open')"), true)
  await wait(5600)
  assert.equal(await evaluate("document.getElementById('letterPaper').hidden"), true, 'Title belongs to the opening animation')
  assert.equal(await evaluate("document.querySelector('#letterMount .ml-paper-title').textContent"), '给你的一封信')
  assert.equal(await evaluate("document.querySelector('#letterMount .ml-paper-small').textContent"), '来自主创团队')
  await screenshot('03b-opening-title.png')
  await wait(800)
  assert.equal(await evaluate("document.getElementById('letterPaper').hidden"), false)
  assert.equal(await evaluate("document.querySelector('#letterPaper .letter-body').firstElementChild.textContent"), '亲爱的你：')
  assert.equal(await evaluate("/来自主创团队|给你的一封信/.test(document.getElementById('letterPaper').textContent)"), false, 'Reading view starts with the salutation')
  assert.equal(await evaluate("document.querySelector('#letterMount .ml-scene').offsetParent"), null, 'Opening title must disappear with the animation')
  assert.equal(await evaluate("document.querySelectorAll('#letterPaper .letter-paragraph').length"), require('../utils/constants').AUTHOR_LETTER.paragraphs.length)
  assert.match(await evaluate("getComputedStyle(document.querySelector('.letter-paragraph')).fontFamily"), /MAMottoWenKai/)
  await screenshot('04-light-letter.png')
  await evaluate("document.querySelector('.letter-scroll').scrollTop=99999; true")
  await wait(150)
  assert.equal(await evaluate("document.getElementById('beginButton').textContent"), '开始旅程')
  const { root } = await cdp('DOM.getDocument')
  const { nodeId } = await cdp('DOM.querySelector', { nodeId: root.nodeId, selector: '#beginButton' })
  const { fonts: buttonFonts } = await cdp('CSS.getPlatformFontsForNode', { nodeId })
  assert.equal(buttonFonts.length, 1, 'All four button characters must use the same font')
  assert.equal(buttonFonts[0].isCustomFont, true, 'Button must use the embedded WenKai font')
  assert.equal(buttonFonts[0].glyphCount, 4, 'All four button glyphs must be embedded')
  await screenshot('05-letter-bottom.png')
  await evaluate("document.getElementById('beginButton').click(); true")
  await wait(100)
  await checkSelectedTab('collection')
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), true, 'Chat guide waits for letter placement')
  await wait(1000)
  assert.equal(await evaluate("document.getElementById('collection').hidden"), false)
  assert.equal(await evaluate("document.getElementById('placedNote').hidden"), false)
  assert.equal(await evaluate("document.querySelectorAll('.ma-arrival-paper').length"), 0)
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), false)
  await screenshot('06-letter-placed.png')
  await wait(2600)
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), false, 'Chat guide stays available after the temporary placement hint')
  assert.equal(await evaluate("document.getElementById('phone').classList.contains('ma-chat-onboarding')"), true)
  assert.equal(await evaluate("document.getElementById('collection').inert"), true)
  assert.equal(await evaluate("document.querySelector('.chat-guide-pointer').getAnimations().length"), 1)
  assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('#tabbar button:disabled')).map(button => button.dataset.page)"), ['home', 'collection', 'profile'])
  for (const page of ['home', 'profile']) {
    await evaluate(`document.querySelector('#tabbar [data-page="${page}"]').click(); true`)
    await checkSelectedTab('collection')
  }
  await evaluate("document.getElementById('letterCard').click(); document.querySelector('[data-kind=photos]').click(); true")
  assert.equal(await evaluate("document.getElementById('letterMount').hidden"), true, 'The spotlight blocks reading the letter behind it')
  assert.equal(await evaluate("document.getElementById('letterCard').hidden"), false, 'The spotlight blocks changing collection categories')
  const guideLayouts = []
  for (const [width, height] of [[320, 568], [360, 800], [390, 844], [430, 932], [1000, 700]]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 })
    await wait(80)
    const layout = await evaluate(`(() => {
      const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
      const phone = rect('#phone'), card = rect('.chat-guide'), button = rect('#chatGuideButton'), tab = rect('#tabbar [data-page=chat]'), pointer = rect('.chat-guide-pointer');
      const target = document.elementFromPoint((tab.left + tab.right) / 2, (tab.top + tab.bottom) / 2);
      const background = document.elementFromPoint(phone.left + 15, phone.top + 110);
      return { width: innerWidth, height: innerHeight, phone, card, button, tab, pointer,
        chatReceivesTap: target.closest('[data-page=chat]') !== null, backgroundBlocked: background.classList.contains('chat-guide-scrim'),
        noOverflow: document.documentElement.scrollWidth <= innerWidth && document.querySelector('.chat-guide').scrollWidth <= document.querySelector('.chat-guide').clientWidth };
    })()`)
    assert.ok(layout.noOverflow, JSON.stringify(layout))
    assert.ok(layout.card.left >= layout.phone.left && layout.card.right <= layout.phone.right, JSON.stringify(layout))
    assert.ok(layout.card.top >= layout.phone.top && layout.button.bottom < layout.tab.top, JSON.stringify(layout))
    assert.ok(layout.pointer.bottom < layout.tab.top + 8, 'The arrow must point toward the chat entrance')
    assert.ok(layout.chatReceivesTap && layout.backgroundBlocked, JSON.stringify(layout))
    guideLayouts.push(layout)
    await screenshot('06-spotlight-' + width + '.png')
  }
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
  assert.equal(await evaluate("document.querySelector('.chat-guide-pointer').getAnimations().length"), 0, 'Reduced motion stops the arrow')
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] })
  await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab' })
  assert.equal(await evaluate("document.activeElement.dataset.page"), 'chat', 'Keyboard focus stays on the chat entrance')
  await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab' })
  assert.equal(await evaluate("document.activeElement.id"), 'chatGuideButton')
  await evaluate("document.getElementById('chatGuideButton').click(); true")
  assert.equal(await evaluate("document.getElementById('chat').hidden"), false)
  assert.equal(await evaluate("document.querySelectorAll('#tabbar button:disabled').length"), 0, 'Navigation unlocks after entering chat')
  await checkSelectedTab('chat')
  await screenshot('08-guided-chat.png')
  for (const page of ['home', 'profile', 'collection']) {
    await evaluate(`document.querySelector('#tabbar [data-page="${page}"]').click(); true`)
    await checkSelectedTab(page)
  }
  await evaluate("document.querySelector('#tabbar [data-page=collection]').click(); true")
  await checkSelectedTab('collection')
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), true, 'The guide is complete after entering chat')
  await evaluate("document.getElementById('letterCard').click(); true")
  await wait(700)
  await evaluate("document.querySelector('#letterMount .ml-envelope').click(); true")
  await wait(6800)
  assert.equal(await evaluate("document.getElementById('beginButton').hidden"), true)
  assert.equal(await evaluate("document.getElementById('backButton').hidden"), false)
  await evaluate("document.getElementById('backButton').click(); document.querySelector('[data-page=profile]').click(); document.getElementById('wechatButton').click(); true")
  assert.equal(await evaluate("document.getElementById('dialogLayer').hidden"), false)
  await evaluate("document.getElementById('dialogClose').click(); document.getElementById('directLetter').click(); true")
  await wait(700)
  await evaluate("document.querySelector('#letterMount .ml-envelope').click(); true")
  await wait(6800)
  await cdp('Emulation.setDeviceMetricsOverride', { width: 360, height: 800, deviceScaleFactor: 1, mobile: true })
  await wait(200)
  assert.equal(await evaluate("document.documentElement.scrollWidth<=innerWidth"), true)
  await screenshot('07-mobile-letter.png')
  await evaluate("document.querySelector('.letter-scroll').scrollTop=99999; document.getElementById('beginButton').click(); true")
  await wait(1500)
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), false)
  // Hit the real highlighted tab through the compositor, rather than calling its handler directly.
  const chatTarget = await evaluate("(() => { const r=document.querySelector('#tabbar [data-page=chat]').getBoundingClientRect(); return { x:r.left+r.width/2,y:r.top+r.height/2 }; })()")
  await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...chatTarget })
  await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...chatTarget })
  assert.equal(await evaluate("document.getElementById('chat').hidden"), false, 'The highlighted tab also opens chat')
  assert.equal(await evaluate("document.getElementById('chatGuide').hidden"), true)
  await checkSelectedTab('chat')
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions))
  fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify({ layouts, guideLayouts, buttonFonts, activeTabFollowsPage: true, chatGuideAfterPlacement: true, spotlightBlocksBackground: true, spotlightBothChatEntrances: true, spotlightReducedMotion: true, chatGuidePersistsUntilChat: true, titleOnlyDuringOpening: true, readingStartsWithSalutation: true, runtimeExceptions: exceptions.length, loginRequiresClick: true, envelopeRequiresTap: true, loginPreviewUsesSimulatedNavigation: true }, null, 2))
  console.log('PASS: offline HTML, manual login, responsive fonts, envelope and letter flow, archive animation, four-page tab highlighting, responsive spotlight, blocked background, keyboard focus, reduced motion, both chat entrances, re-read and profile login dialog.')
  console.log('Screenshots:', output)
})().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => { if (socket) socket.close(); browser.kill() })
