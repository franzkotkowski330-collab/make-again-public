const { test, beforeEach } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const service = require('../utils/service')
let saved
const clone = value => value === undefined ? '' : JSON.parse(JSON.stringify(value))
beforeEach(() => {
  saved = undefined
  global.wx = { getStorageSync: () => clone(saved), setStorageSync: (_key, state) => { saved = clone(state) } }
})
test('guided answers update the profile atomically, survive reload and stop asking when complete', () => {
  service.acceptConsent(); service.startChat(); service.startChat()
  assert.equal(service.listMessages().length, 2)
  assert.equal(service.getOverview().messageCount, 0)
  assert.equal(service.getChatState().step.key, 'nickname')
  service.sendChatMessage({ text: ' 岚芝 ' }, 'nickname-operation')
  service.sendChatMessage({ text: '岚芝' }, 'nickname-operation')
  assert.equal(service.getChatState().step.key, 'lifeStage')
  assert.equal(service.getProfile().nickname.value, '岚芝')
  assert.match(service.listMessages().at(-1).content, /岚芝/)
  const before = service.listMessages().length
  service.startChat(); assert.equal(service.listMessages().length, before)
  service.sendChatMessage({ text: '还在读书' })
  service.sendChatMessage({ text: '最近有点迷茫' })
  service.sendChatMessage({ text: '先听我说说' })
  assert.equal(service.getChatState().step, null)
  assert.equal(service.getChatState().completed, 4)
  const complete = service.listMessages().length
  service.startChat(); assert.equal(service.listMessages().length, complete)
  service.sendChatMessage({ text: '今天想记录一点小事' })
  assert.equal(service.listMessages().length, complete + 1)
  assert.equal(service.getProfile().expectation.value, '先听我说说')
  assert.equal(service.getOverview().messageCount, 5)
})
test('skipping never invents an answer and cleared fields can be asked again', () => {
  service.acceptConsent(); service.startChat()
  for (let i = 0; i < 4; i++) service.sendChatMessage({ skip: true })
  assert.equal(service.getChatState().completed, 4)
  assert.equal(service.getProfile().nickname.declined, true)
  assert.equal(service.getProfile().nickname.value, '')
  const profile = service.getProfile(); profile.concern.declined = false
  service.saveProfile(profile); service.startChat()
  assert.equal(service.getChatState().step.key, 'concern')
  assert.equal(service.listMessages().at(-1).guideKey, 'concern')
})
test('voice answers remain audio references, preserve through edits and can be replaced by text', () => {
  service.acceptConsent(); service.startChat()
  const message = service.sendChatMessage({ audio: { filePath: 'wxfile://saved/voice.mp3', duration: 12 } })
  assert.equal(message.type, 'voice')
  assert.equal(service.getProfile().nickname.voiceMessageId, message.id)
  assert.equal(service.getProfile().nickname.value, '')
  assert.equal(service.getChatState().step.key, 'lifeStage')
  assert.equal(service.getOverview().profileCount, 1)
  const profile = service.getProfile(); service.saveProfile(profile)
  assert.equal(service.getProfile().nickname.voiceMessageId, message.id)
  profile.nickname.value = '岚芝'; service.saveProfile(profile)
  assert.equal(service.getProfile().nickname.voiceMessageId, undefined)
  assert.equal(service.listMessages().find(item => item.id === message.id).audio.duration, 12)
})
test('older four-field profiles remain readable and the added questions do not erase history', () => {
  service.acceptConsent(); service.saveMessage('以前的记录')
  const profile = saved.journeys[0].profile
  delete profile.nickname; delete profile.lifeStage
  const before = clone(saved)
  assert.equal(service.getOverview().hasGuest, true)
  assert.equal(service.getProfile().nickname.value, '')
  assert.deepEqual(saved, before, 'Reading does not migrate storage in place')
  service.startChat()
  assert.equal(service.listMessages()[0].content, '以前的记录')
  assert.equal(service.getChatState().step.key, 'nickname')
})
test('invalid input and a failed write leave messages and profile unchanged', () => {
  service.acceptConsent(); service.startChat()
  const before = clone(saved)
  assert.throws(() => service.sendChatMessage({ text: '字'.repeat(31) }), { code: 'MESSAGE_TOO_LONG' })
  assert.throws(() => service.sendChatMessage({ audio: { filePath: 'some-file', duration: 0 } }), { code: 'INVALID_AUDIO' })
  wx.setStorageSync = () => { throw new Error('quota') }
  assert.throws(() => service.sendChatMessage({ text: '岚芝' }), { code: 'STORAGE_WRITE_FAILED' })
  assert.deepEqual(saved, before)
})
function loadPage() {
  let page, authorization, callbacks = {}, pendingSave, starts = 0, stops = 0, submissions = [], failing = false
  const intervals = new Map(), toasts = []
  const recorder = {
    onStart: callback => { callbacks.start = callback }, onStop: callback => { callbacks.stop = callback },
    onError: callback => { callbacks.error = callback }, onInterruptionBegin: callback => { callbacks.interrupt = callback },
    start() { starts++; callbacks.start() }, stop() { stops++ }
  }
  const context = {
    Page: value => { page = value }, getApp: () => ({ globalData: {} }),
    setInterval: callback => { intervals.set(1, callback); return 1 }, clearInterval: id => intervals.delete(id),
    require: name => name.includes('service') ? { sendChatMessage(value) { if (failing) throw new Error('保存失败'); submissions.push(value) } } : { showError: error => toasts.push(error.message), formatTime: () => '' },
    wx: { authorize: options => { authorization = options }, getRecorderManager: () => recorder, showToast: options => toasts.push(options.title), showModal() {}, getFileSystemManager: () => ({ saveFile: options => { pendingSave = options } }) }
  }
  vm.runInNewContext(fs.readFileSync(require.resolve('../pages/chat/chat.js'), 'utf8'), context)
  page.data = clone(page.data); page.setData = patch => Object.assign(page.data, patch); page.refresh = () => {}; page._active = true
  return { page, callbacks, intervals, toasts, submissions, authorize: () => authorization.success(), stopResult: result => callbacks.stop(result), get starts() { return starts }, get stops() { return stops }, get pendingSave() { return pendingSave }, failWrites: () => { failing = true }, allowWrites: () => { failing = false } }
}
test('releasing before microphone authorization prevents late recording, including rapid retries', () => {
  const session = loadPage(), event = { touches: [{ clientY: 500 }] }
  session.page.startRecording(event)
  session.page.finishRecording()
  session.authorize(); assert.equal(session.starts, 0)
  session.page.startRecording(event); session.authorize(); assert.equal(session.starts, 1)
  session.page.finishRecording(); assert.equal(session.stops, 1)
  session.stopResult({ tempFilePath: 'temp', duration: 1500 })
  assert.equal(session.page.data.pendingAudio.duration, 2)
  assert.equal(session.intervals.size, 0)
})
test('slide cancellation, interruptions and leaving discard recordings instead of sending them', () => {
  for (const action of ['slide', 'interrupt', 'leave']) {
    const session = loadPage()
    session.page.startRecording({ touches: [{ clientY: 500 }] }); session.authorize()
    if (action === 'slide') { session.page.moveRecording({ touches: [{ clientY: 400 }] }); session.page.finishRecording() }
    if (action === 'interrupt') session.callbacks.interrupt()
    if (action === 'leave') session.page.leave()
    session.stopResult({ tempFilePath: 'temp', duration: 3000 })
    assert.equal(session.page.data.pendingAudio, null, action)
    assert.equal(session.submissions.length, 0)
    assert.equal(session.intervals.size, 0)
  }
})
test('voice save failure retains the moved audio for retry without saving or submitting twice', () => {
  const session = loadPage()
  session.page.setData({ pendingAudio: { filePath: 'temp', duration: 3 } })
  session.page.sendAudio(); session.page.sendAudio()
  session.failWrites(); session.pendingSave.success({ savedFilePath: 'permanent' })
  assert.equal(session.page.data.pendingAudio.filePath, 'permanent')
  assert.equal(session.page.data.pendingAudio.persisted, true)
  session.allowWrites(); session.page.sendAudio()
  assert.equal(session.submissions.length, 1)
  assert.equal(session.submissions[0].audio.filePath, 'permanent')
  assert.equal(session.page.data.pendingAudio, null)
})
