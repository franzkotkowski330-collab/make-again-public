const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { pathToFileURL } = require('node:url')
const { spawn } = require('node:child_process')
const assert = require('node:assert/strict')
const WebSocket = require('ws')
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const output = path.resolve(__dirname, '../artifacts/ui/chat-preview')
fs.mkdirSync(output, { recursive: true })
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'make-again-chat-browser-'))
const browser = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--remote-debugging-port=9428', '--user-data-dir=' + profile, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
let socket, serial = 0
const pending = new Map(), exceptions = [], layouts = []
function cdp(method, params = {}) { return new Promise((resolve, reject) => { const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })) }) }
async function evaluate(expression) {
  const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click(); true`)
async function screenshot(name) { const shot = await cdp('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(output, name), Buffer.from(shot.data, 'base64')) }
async function type(text) {
  await evaluate(`(() => {const input = document.querySelector('.chat-input');input.focus();input.value=${JSON.stringify(text)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`)
  await wait(30)
}
const state = () => evaluate("JSON.parse(localStorage.getItem('preview_make_again_local_v1'))")
async function press(selector, type) {
  const point = await evaluate(`(() => { const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})()`)
  await cdp('Input.dispatchMouseEvent', { type, button: 'left', clickCount: 1, ...point })
}
;(async () => {
  let tabs
  for (let attempt = 0; attempt < 40; attempt++) { try { tabs = await (await fetch('http://127.0.0.1:9428/json/list')).json(); break } catch (_) { await wait(150) } }
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject) })
  socket.on('message', raw => {
    const event = JSON.parse(raw)
    if (event.method === 'Runtime.exceptionThrown') exceptions.push(event.params)
    if (pending.has(event.id)) { const callbacks = pending.get(event.id); pending.delete(event.id); event.error ? callbacks.reject(event.error) : callbacks.resolve(event.result) }
  })
  await cdp('Page.enable'); await cdp('Runtime.enable')
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  const url = pathToFileURL(path.resolve(__dirname, '../Make-Again-预览.html')).href + '#chat'
  await cdp('Page.navigate', { url }); await wait(450)
  assert.equal(await evaluate("document.getElementById('chat').hidden"), false)
  assert.equal(await state(), null, 'Opening chat alone does not create a profile')
  await screenshot('01-consent.png')
  await click('.accept-consent'); await wait(70)
  await screenshot('02-after-consent.png')
  assert.equal(await evaluate("document.querySelectorAll('.from-companion').length"), 2)
  assert.match(await evaluate("document.querySelector('.message-content').textContent"), /你希望我怎么称呼你/)
  await screenshot('02-first-question.png')
  await type('岚芝'); await click('.send-button'); await wait(70)
  assert.equal((await state()).journeys[0].profile.nickname.value, '岚芝')
  assert.match(await evaluate("document.querySelector('.message-content').textContent"), /很高兴认识你/)
  assert.equal(await evaluate("document.querySelectorAll('.quick-reply').length"), 4)
  await type('我的草稿'); await click('.quick-reply')
  assert.equal(await evaluate("document.querySelector('.chat-input').value"), '我的草稿', 'Quick replies cannot replace a draft')
  await evaluate("document.getElementById('chatToast').hidden=true")
  await type(''); await click('.quick-reply'); await wait(70)
  assert.equal((await state()).journeys[0].profile.lifeStage.value, '还在读书')
  await screenshot('03-profile-guide.png')
  await click('.mode-button'); await wait(50)
  await press('.hold-to-talk', 'mousePressed'); await wait(2200)
  assert.ok(await evaluate("document.querySelector('.recording-indicator') !== null"), 'Browser records actual audio using the fake microphone')
  await screenshot('04-recording.png')
  await press('.hold-to-talk', 'mouseReleased'); await wait(250)
  assert.ok(await evaluate("document.querySelector('.audio-review') !== null"))
  const beforeAudio = (await state()).messages.length
  await click('.review-play'); await wait(150)
  assert.ok(await evaluate("document.querySelector('.review-play').classList.contains('is-playing')"))
  await screenshot('05-listen-before-sending.png')
  await click('.audio-send'); await wait(250)
  const audioState = await state(), voice = audioState.messages.find(message => message.type === 'voice')
  assert.match(voice.audio.filePath, /^ma-audio:/)
  assert.equal(audioState.journeys[0].profile.concern.voiceMessageId, voice.id)
  assert.equal(audioState.messages.length, beforeAudio + 2)
  await click('.guide-skip-row button'); await wait(80)
  assert.equal((await state()).journeys[0].profile.expectation.declined, true)
  assert.equal(await evaluate("document.querySelectorAll('.guide-skip-row').length"), 0)
  await click('.mode-button'); await wait(30)
  const longText = '今天也想认真照顾自己的感受。'.repeat(18)
  await type(longText); await click('.send-button'); await wait(60)
  assert.equal((await state()).messages.at(-1).content, longText)
  await evaluate("document.querySelector('.message-scroll').scrollTop=0")
  await screenshot('06-chat-conversation.png')
  await click('.profile-strip-button'); await wait(50)
  assert.match(await evaluate("document.getElementById('chatProfileEditor').textContent"), /已通过语音回答/)
  await screenshot('07-personal-profile.png')
  await click('#chatProfileEditor button:last-child'); await wait(30)
  const previousCount = (await state()).messages.length
  await cdp('Page.reload'); await wait(450)
  assert.equal((await state()).messages.length, previousCount)
  assert.equal(await evaluate("document.querySelectorAll('.guide-skip-row').length"), 0)
  await evaluate(`document.querySelector('[data-id="${voice.id}"]').click()`); await wait(180)
  assert.equal(await evaluate(`document.querySelector('[data-id="${voice.id}"]').classList.contains('is-playing')`), true, 'IndexedDB audio survives reload and plays')
  for (const [width, height] of [[320, 568], [360, 800], [390, 844], [430, 932], [1000, 900]]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 }); await wait(80)
    const layout = await evaluate(`(() => {
      const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};
      const messages=document.querySelector('.message-content');
      return {width:innerWidth,height:innerHeight,input:rect('.input-row'),tabbar:rect('#tabbar'),phone:rect('#phone'),noOverflow:document.documentElement.scrollWidth<=innerWidth&&messages.scrollWidth<=messages.clientWidth};
    })()`)
    assert.ok(layout.noOverflow, JSON.stringify(layout))
    assert.ok(layout.input.bottom <= layout.tabbar.top, JSON.stringify(layout))
    assert.ok(layout.input.left >= layout.phone.left && layout.input.right <= layout.phone.right, JSON.stringify(layout))
    layouts.push(layout); await screenshot('08-chat-' + width + '.png')
  }
  assert.equal(await evaluate("document.querySelectorAll('#chat input[type=file]').length"), 0)
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions))
  fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify({ layouts, nativeTemplateAndController: true, guidedProfile: true, draftPreserved: true, realMediaRecorderWithTestMicrophone: true, reviewBeforeSend: true, audioPersistsAndPlaysAfterReload: true, skipOptionalQuestion: true, noImageUpload: true, exceptions }, null, 2))
  console.log('PASS: chat consent, four profile prompts, draft protection, real recording and preview, persisted voice playback, optional questions, profile editor, reload and five responsive layouts.')
  console.log('Screenshots:', output)
})().catch(error => { console.error(error); if (exceptions.length) console.error(JSON.stringify(exceptions, null, 2)); process.exitCode = 1 }).finally(() => { if (socket) socket.close(); browser.kill() })
