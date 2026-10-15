const { test, beforeEach } = require('node:test')
const assert = require('node:assert/strict')
const storage = require('../utils/storage')
const service = require('../utils/service')
const { PROFILE_FIELDS } = require('../utils/constants')
let saved, writes
const clone = value => value === undefined ? '' : JSON.parse(JSON.stringify(value))
beforeEach(() => {
  saved = undefined; writes = 0
  global.wx = { getStorageSync: () => clone(saved), setStorageSync: (_key, state) => { saved = clone(state); writes++ } }
})
test('read-only entry and declined consent do not create a guest or persist a message', () => {
  assert.equal(service.getOverview().hasGuest, false)
  assert.deepEqual(service.listMessages(), [])
  assert.throws(() => service.saveMessage('未同意的倾诉'), { code: 'CONSENT_REQUIRED' })
  assert.throws(() => service.getProfile(), { code: 'CONSENT_REQUIRED' })
  assert.equal(writes, 0); assert.equal(saved, undefined)
})
test('author-letter read state persists without creating a guest', () => {
  service.markLetterRead()
  assert.equal(service.getOverview().letterRead, true)
  assert.equal(saved.guest, null); assert.deepEqual(saved.journeys, [])
})
test('consent creates one journey and is idempotent; no personal field is compulsory', () => {
  service.acceptConsent(); const guestId = saved.guest.id
  service.acceptConsent()
  assert.equal(saved.guest.id, guestId); assert.equal(saved.journeys.length, 1)
  assert.equal(service.getOverview().profileCount, 0)
  assert.deepEqual(Object.keys(service.getProfile()), PROFILE_FIELDS.map(field => field.key))
})
test('messages survive service reload and repeated operation IDs do not duplicate them', () => {
  service.acceptConsent()
  service.saveMessage('  今天很想念外公。  ', 'same-operation')
  service.saveMessage('今天很想念外公。', 'same-operation')
  delete require.cache[require.resolve('../utils/local-service')]
  const restarted = require('../utils/local-service')
  assert.equal(restarted.listMessages().length, 1)
  assert.equal(restarted.listMessages()[0].content, '今天很想念外公。')
  assert.equal(restarted.listMessages()[0].role, 'user')
})
test('another owner or journey cannot leak into the current message list', () => {
  service.acceptConsent(); service.saveMessage('本段旅程')
  saved.messages.push({ id: 'other-journey', journeyId: 'elsewhere', ownerId: saved.guest.id, content: '其他旅程' })
  saved.messages.push({ id: 'other-owner', journeyId: saved.currentJourneyId, ownerId: 'another-user', content: '其他人' })
  assert.deepEqual(service.listMessages().map(m => m.content), ['本段旅程'])
  assert.equal(service.getOverview().messageCount, 1)
})
test('four fields save; refusal clears an earlier answer; invalid updates are atomic', () => {
  service.acceptConsent()
  const profile = service.getProfile()
  profile.lossType.value = 'relative'; profile.relationship.value = '外公'; profile.concern.value = '想念'; profile.expectation.value = '只想被倾听'
  service.saveProfile(profile); assert.equal(service.getOverview().profileCount, 4)
  profile.relationship.declined = true; service.saveProfile(profile)
  assert.equal(service.getProfile().relationship.value, ''); assert.equal(service.getProfile().relationship.declined, true)
  const before = clone(saved); profile.lossType.value = 'invalid'
  assert.throws(() => service.saveProfile(profile), { code: 'INVALID_PROFILE' })
  assert.deepEqual(saved, before)
})
test('blank and oversized messages cannot enter saved history', () => {
  service.acceptConsent()
  assert.throws(() => service.saveMessage('   '), { code: 'EMPTY_MESSAGE' })
  assert.throws(() => service.saveMessage('字'.repeat(2001)), { code: 'MESSAGE_TOO_LONG' })
  assert.equal(service.listMessages().length, 0)
})
test('failed writes report failure and do not mutate previously saved records', () => {
  service.acceptConsent(); const before = clone(saved)
  wx.setStorageSync = () => { throw new Error('quota exceeded') }
  assert.throws(() => service.saveMessage('保存失败的内容'), { code: 'STORAGE_WRITE_FAILED' })
  assert.deepEqual(saved, before)
})
test('unsupported or corrupt cache stops writes instead of silently overwriting content', () => {
  saved = { schemaVersion: 9, original: '需要保留的数据' }
  assert.throws(() => service.acceptConsent(), { code: 'STORAGE_INVALID' })
  assert.equal(writes, 0); assert.equal(saved.original, '需要保留的数据')
})
