const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

function load(relative, overrides = {}) {
  const timers = new Map()
  let serial = 0, definition
  const context = {
    Page: value => { definition = value }, Component: value => { definition = value },
    require: module => overrides.require ? overrides.require(module) : require(path.resolve(__dirname, '..', path.dirname(relative), module)),
    setTimeout: (callback, delay) => { timers.set(++serial, { callback, delay }); return serial },
    clearTimeout: id => timers.delete(id),
    wx: overrides.wx || {}, getApp: () => overrides.app || { globalData: {} }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '..', relative), 'utf8'), context)
  const instance = Object.assign({}, definition.methods || definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(value) { Object.assign(this.data, value) }
  })
  return { definition, instance, timers, fire(delay) { const match = [...timers].find(([, timer]) => timer.delay === delay); assert.ok(match, 'Missing timer ' + delay); timers.delete(match[0]); match[1].callback() } }
}

test('login stays on screen after its animation and only calls wx.login when tapped', () => {
  const routes = [], logins = []
  const { instance, timers } = load('pages/login/login.js', { wx: { nextTick: callback => callback(), login: options => logins.push(options), switchTab: options => routes.push(options), redirectTo: options => routes.push(options) } })
  instance.onShow()
  assert.equal(instance.data.animate, true)
  assert.equal(timers.size, 0)
  assert.equal(logins.length, 0)
  assert.equal(routes.length, 0)
  instance.loginWithWechat()
  instance.loginWithWechat()
  assert.equal(logins.length, 1)
  assert.equal(instance.data.isLoggingIn, true)
  assert.equal(routes.length, 0)
  logins[0].success({ code: 'test-code' })
  assert.equal(routes[0].url, '/pages/letter/letter?first=1', 'Login must always land on the envelope first')
  instance.loginWithWechat()
  assert.equal(routes.length, 1)
})

test('failed or empty WeChat credentials stay on login and allow retry', () => {
  const logins = [], routes = []
  const { instance } = load('pages/login/login.js', { wx: { nextTick: callback => callback(), login: options => logins.push(options), switchTab: options => routes.push(options), redirectTo: options => routes.push(options) } })
  instance.onShow()
  instance.loginWithWechat()
  logins[0].fail({ errMsg: 'timeout' })
  assert.equal(instance.data.isLoggingIn, false)
  assert.ok(instance.data.loginError)
  instance.loginWithWechat()
  assert.equal(instance.data.loginError, '')
  logins[1].success({ code: '' })
  assert.equal(instance.data.isLoggingIn, false)
  assert.equal(routes.length, 0)
  instance.loginWithWechat()
  logins[2].success({ code: 'retry-code' })
  assert.equal(routes.length, 1)
})

test('failed letter navigation falls back to home, and a failed home navigation releases the login button', () => {
  const logins = [], routes = []
  const { instance } = load('pages/login/login.js', { wx: { nextTick: callback => callback(), login: options => logins.push(options), switchTab: options => routes.push(options), redirectTo: options => routes.push(options) } })
  instance.onShow()
  instance.loginWithWechat()
  logins[0].success({ code: 'first-code' })
  routes[0].fail()
  assert.equal(routes[1].url, '/pages/home/home', 'A failed letter redirect falls back to home')
  assert.equal(instance.data.isLoggingIn, true)
  routes[1].fail()
  assert.equal(instance.data.isLoggingIn, false)
  assert.ok(instance.data.loginError)
  instance.loginWithWechat()
  logins[1].success({ code: 'new-code' })
  assert.equal(routes.length, 3)
})

test('hidden or unloaded login ignores late callbacks and can restart after showing', () => {
  const logins = [], routes = []
  const { instance } = load('pages/login/login.js', { wx: { nextTick: callback => callback(), login: options => logins.push(options), switchTab: options => routes.push(options), redirectTo: options => routes.push(options) } })
  instance.onShow()
  instance.loginWithWechat()
  instance.onHide()
  logins[0].success({ code: 'background-code' })
  assert.equal(routes.length, 0)
  instance.onShow()
  assert.equal(instance.data.isLoggingIn, false)
  instance.loginWithWechat()
  logins[0].fail()
  assert.equal(instance.data.isLoggingIn, true, 'A stale failure must not interrupt the new attempt')
  logins[1].success({ code: 'current-code' })
  assert.equal(routes.length, 1)
  instance.onUnload()
  routes[0].fail()
  routes[1].fail()
  assert.equal(instance.data.loginError, '')
})

test('home opens the first letter, routes it to collection and recovers from navigation failure', () => {
  const app = { globalData: {} }
  let reads = 0, folded = 0, reset = 0, route
  const letter = { archive() { folded++ }, resetClosing() { reset++ } }
  const service = { getOverview: () => ({ hasGuest: false, letterRead: false }), markLetterRead: () => { reads++ } }
  const { instance } = load('pages/home/home.js', {
    app, require: module => module.endsWith('service') ? service : { showError() {} },
    wx: { hideTabBar() {}, showTabBar() {}, switchTab: options => { route = options } }
  })
  instance.selectComponent = () => letter
  instance.onShow()
  assert.equal(instance.data.showFirstLetter, true)
  instance.beginLetter()
  instance.beginLetter()
  assert.equal(reads, 1)
  assert.equal(folded, 1)
  instance.archiveLetter()
  assert.equal(app.globalData.pendingLetterArrival, true)
  assert.equal(route.url, '/pages/collection/collection')
  route.fail()
  assert.equal(app.globalData.pendingLetterArrival, false)
  assert.equal(instance.data.savingLetter, false)
  assert.equal(instance.data.showFirstLetter, true)
  assert.equal(reset, 1)
  instance.beginLetter()
  instance.archiveLetter()
  route.success()
  assert.equal(instance.data.showFirstLetter, false)
})

test('failed letter persistence keeps the letter readable without starting an animation', () => {
  const { instance } = load('pages/home/home.js', {
    require: module => module.endsWith('service') ? { markLetterRead() { throw new Error('quota') } } : { showError() {} }
  })
  instance.selectComponent = () => { throw new Error('Animation must not start') }
  instance.beginLetter()
  assert.equal(instance.data.savingLetter, false)
})

test('envelope stays closed until tapped, timers stop on background, and the letter resumes readable', () => {
  const events = []
  const { definition, instance, timers } = load('components/author-letter/author-letter.js')
  instance.triggerEvent = name => events.push(name)
  assert.equal(instance.data.opening, false, 'Must not open on attach alone')
  definition.pageLifetimes.show.call(instance)
  assert.equal(instance.data.opening, false, 'Show must not auto-open the envelope')
  instance.openLetter()
  assert.equal(instance.data.opening, true)
  definition.pageLifetimes.show.call(instance)
  assert.equal(instance.data.reading, false, 'Initial show must not skip the opening animation')
  definition.pageLifetimes.hide.call(instance)
  assert.equal(timers.size, 0)
  definition.pageLifetimes.show.call(instance)
  assert.equal(instance.data.reading, true)
  instance.archive()
  definition.pageLifetimes.hide.call(instance)
  definition.pageLifetimes.show.call(instance)
  assert.equal(instance.data.closing, false)
  assert.deepEqual(events, ['cancel'])
})

test('pending arrival selects letters even on an existing collection tab and is consumed once', () => {
  const app = { globalData: { pendingLetterArrival: true } }
  let placements = 0
  const { instance } = load('pages/collection/collection.js', { app, wx: { pageScrollTo() {}, nextTick: callback => callback() } })
  instance.data.active = 'photos'
  instance.refresh = () => {}
  instance.placeLetter = () => { placements++ }
  instance.onShow()
  assert.equal(instance.data.active, 'letters')
  assert.equal(instance.data.letterArriving, true)
  assert.equal(app.globalData.pendingLetterArrival, false)
  instance.onHide()
  instance.onShow()
  assert.equal(instance.data.letterArriving, false)
  assert.equal(placements, 1)
})

test('spotlight waits for letter placement and restores native navigation when leaving', () => {
  const app = { globalData: { pendingLetterArrival: true } }, navigation = []
  const { instance } = load('pages/collection/collection.js', {
    app, wx: { pageScrollTo() {}, nextTick: callback => callback(), hideTabBar: () => navigation.push('hide'), showTabBar: () => navigation.push('show') }
  })
  instance.refresh = () => {}
  instance.placeLetter = () => {}
  instance.onShow()
  assert.equal(instance.data.showChatGuide, true)
  assert.deepEqual(navigation, [], 'The real tab bar stays visible while the letter lands')
  instance.finishArrival()
  assert.deepEqual(navigation, ['hide'])
  instance.onHide()
  assert.deepEqual(navigation, ['hide', 'show'])
  instance.onShow()
  assert.deepEqual(navigation, ['hide', 'show', 'hide'], 'An unfinished invitation returns with the page')
  instance.onUnload()
  assert.deepEqual(navigation, ['hide', 'show', 'hide', 'show'])
})

test('spotlight chat navigation prevents duplicate taps, retries failures and restores tabs after success', () => {
  const app = { globalData: { pendingChatGuide: true } }, routes = [], navigation = [], errors = []
  const { instance } = load('pages/collection/collection.js', {
    app, require: module => module.endsWith('/ui') ? { showError: error => errors.push(error.message) } : require(path.resolve(__dirname, '../pages/collection', module)),
    wx: { hideTabBar: () => navigation.push('hide'), showTabBar: () => navigation.push('show'), switchTab: options => routes.push(options) }
  })
  instance.refresh = () => {}
  instance.onShow()
  instance.startChat()
  instance.startChat()
  assert.equal(routes.length, 1)
  assert.equal(routes[0].url, '/pages/chat/chat')
  routes[0].fail(); routes[0].complete()
  assert.equal(errors.length, 1)
  assert.equal(instance.data.openingChat, false)
  assert.equal(app.globalData.pendingChatGuide, true)
  assert.deepEqual(navigation, ['hide'], 'Failed navigation keeps the spotlight available')
  instance.startChat()
  routes[1].success(); routes[1].complete()
  assert.equal(instance.data.showChatGuide, false)
  assert.equal(app.globalData.pendingChatGuide, false)
  assert.deepEqual(navigation, ['hide', 'show'])
  instance.onHide(); instance.onShow()
  assert.equal(instance.data.showChatGuide, false, 'Completed invitations stay completed')
})
