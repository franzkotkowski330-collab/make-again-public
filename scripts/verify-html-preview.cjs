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
  await cdp('Page.enable'); await cdp('Runtime.enable')
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
  await wait(6400)
  assert.equal(await evaluate("document.getElementById('letterPaper').hidden"), false)
  assert.equal(await evaluate("document.querySelectorAll('#letterPaper .letter-paragraph').length"), require('../utils/constants').AUTHOR_LETTER.paragraphs.length)
  assert.match(await evaluate("getComputedStyle(document.querySelector('.letter-paragraph')).fontFamily"), /MAMottoWenKai/)
  await screenshot('04-light-letter.png')
  await evaluate("document.querySelector('.letter-scroll').scrollTop=99999; true")
  await wait(150)
  assert.equal(await evaluate("document.getElementById('beginButton').textContent"), '开始旅程')
  await screenshot('05-letter-bottom.png')
  await evaluate("document.getElementById('beginButton').click(); true")
  await wait(1100)
  assert.equal(await evaluate("document.getElementById('collection').hidden"), false)
  assert.equal(await evaluate("document.getElementById('placedNote').hidden"), false)
  assert.equal(await evaluate("document.querySelectorAll('.ma-arrival-paper').length"), 0)
  await screenshot('06-letter-placed.png')
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
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions))
  fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify({ layouts, runtimeExceptions: exceptions.length, loginRequiresClick: true, envelopeRequiresTap: true, loginPreviewUsesSimulatedNavigation: true }, null, 2))
  console.log('PASS: offline HTML, manual login, unclipped Parisienne ink at 320/360/390/430px, reduced motion, tap-to-open envelope, full letter/font, archive animation, re-read and profile login dialog.')
  console.log('Screenshots:', output)
})().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => { if (socket) socket.close(); browser.kill() })
