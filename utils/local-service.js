const storage = require('./storage')
const { PROFILE_FIELDS, LOSS_TYPES, AUTHOR_LETTER } = require('./constants')
const { GREETING, getGuide, question } = require('./chat-guide')
const id = prefix => prefix + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10)
function fail(code, message) { throw Object.assign(new Error(message), { code }) }
function currentJourney(state) {
  if (!state.consent || !state.guest) fail('CONSENT_REQUIRED', '请先阅读并同意本地记录说明。')
  const journey = state.journeys.find(j => j.id === state.currentJourneyId && j.ownerId === state.guest.id)
  if (!journey) fail('JOURNEY_NOT_FOUND', '没有找到当前旅程，请返回首页重试。')
  // Older local journeys have only the original four fields. Reading adds defaults in memory.
  PROFILE_FIELDS.forEach(field => {
    if (!journey.profile[field.key]) journey.profile[field.key] = { value: '', declined: false, updatedAt: journey.createdAt }
  })
  return journey
}
function acceptConsent() {
  const existing = storage.read()
  if (existing.consent && existing.guest) { currentJourney(existing); return getOverview() }
  storage.update(state => {
    const now = Date.now(), guestId = id('guest'), journeyId = id('journey')
    state.consent = { version: 'local-v1', acceptedAt: now }
    state.guest = { id: guestId, createdAt: now }
    state.currentJourneyId = journeyId
    const profile = {}
    PROFILE_FIELDS.forEach(field => { profile[field.key] = { value: '', declined: false, updatedAt: now } })
    state.journeys.push({ id: journeyId, ownerId: guestId, createdAt: now, status: 'active', profile })
  })
  return getOverview()
}
function getOverview() {
  const state = storage.read()
  if (!state.consent || !state.guest) return { letterRead: state.letterRead, hasGuest: false, journey: null, messageCount: 0, profileCount: 0 }
  const journey = currentJourney(state)
  const messages = state.messages.filter(m => m.journeyId === journey.id && m.ownerId === state.guest.id && m.role === 'user')
  return {
    letterRead: state.letterRead, hasGuest: true, journey,
    lossLabel: (LOSS_TYPES.find(t => t.value === journey.profile.lossType.value) || LOSS_TYPES[0]).label,
    messageCount: messages.length,
    profileCount: PROFILE_FIELDS.filter(f => journey.profile[f.key].value || journey.profile[f.key].declined || journey.profile[f.key].voiceMessageId).length,
    createdDate: formatDate(journey.createdAt), lastMessage: messages.length ? messages[messages.length - 1] : null
  }
}
function formatDate(ms) { const d = new Date(ms); return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('.') }
function markLetterRead() { storage.update(state => { state.letterRead = true }) }
function listMessages() {
  const state = storage.read()
  if (!state.consent || !state.guest) return []
  const journey = currentJourney(state)
  return state.messages.filter(m => m.ownerId === state.guest.id && m.journeyId === journey.id)
}
function saveMessage(text, clientId) {
  if (typeof text !== 'string' || !text.trim()) fail('EMPTY_MESSAGE', '可以先写一点想说的话。')
  const content = text.trim()
  if (content.length > 2000) fail('MESSAGE_TOO_LONG', '这次最多保存 2000 个字，可以分几次写下。')
  let result
  storage.update(state => {
    const journey = currentJourney(state)
    const duplicate = clientId && state.messages.find(m => m.clientId === clientId && m.journeyId === journey.id && m.ownerId === state.guest.id)
    if (duplicate) { result = duplicate; return }
    result = { id: id('message'), clientId: clientId || id('send'), journeyId: journey.id, ownerId: state.guest.id, role: 'user', content, createdAt: Date.now() }
    state.messages.push(result)
  })
  return result
}
function getProfile() { return currentJourney(storage.read()).profile }
function appendGuide(state, journey) {
  const messages = state.messages.filter(m => m.journeyId === journey.id && m.ownerId === state.guest.id)
  const append = (content, guideKey) => state.messages.push({ id: id('guide'), journeyId: journey.id, ownerId: state.guest.id, role: 'assistant', type: 'text', content, guideKey, createdAt: Date.now() })
  if (!messages.some(m => m.guideKey === 'welcome')) append(GREETING, 'welcome')
  const guide = getGuide(journey.profile)
  const key = guide.step ? guide.step.key : 'complete'
  // Reopen a question when its profile field has been cleared later in the editor.
  const last = messages[messages.length - 1]
  if (guide.step ? (!last || last.guideKey !== key) : !messages.some(m => m.guideKey === key)) {
    append(guide.step ? question(guide.step, journey.profile) : '谢谢你愿意让我了解你。你的个人档案已经整理好了。\n以后想补充或修改，可以随时去「我的」查看。接下来，把这里当作一个记录心事的角落吧。', key)
  }
}
function startChat() {
  const state = storage.read(), count = state.messages.length
  appendGuide(state, currentJourney(state))
  if (state.messages.length !== count) storage.write(state)
}
function getChatState() {
  const profile = getProfile()
  return Object.assign({ messages: listMessages(), profile }, getGuide(profile))
}
function sendChatMessage(payload, clientId) {
  if (!payload || typeof payload !== 'object') fail('EMPTY_MESSAGE', '可以先写一点想说的话。')
  const text = typeof payload.text === 'string' ? payload.text.trim() : ''
  const audio = payload.audio
  if (!text && !audio && !payload.skip) fail('EMPTY_MESSAGE', '可以先写一点想说的话。')
  if (audio && (typeof audio.filePath !== 'string' || !audio.filePath || !Number.isFinite(audio.duration) || audio.duration < 1 || audio.duration > 60)) fail('INVALID_AUDIO', '这段语音暂时无法保存，请重新录制。')
  let result
  storage.update(state => {
    const journey = currentJourney(state)
    const duplicate = clientId && state.messages.find(m => m.clientId === clientId && m.journeyId === journey.id && m.ownerId === state.guest.id)
    if (duplicate) { result = duplicate; return }
    const guide = getGuide(journey.profile)
    if (payload.skip && !guide.step) fail('NO_QUESTION', '当前没有待回答的问题。')
    if (text.length > guide.maxLength) fail('MESSAGE_TOO_LONG', '这次最多输入 ' + guide.maxLength + ' 个字，可以缩短后再发送。')
    result = { id: id('message'), clientId: clientId || id('send'), journeyId: journey.id, ownerId: state.guest.id, role: 'user', type: audio ? 'voice' : 'text', content: payload.skip ? '这个问题先跳过' : text, createdAt: Date.now() }
    if (audio) result.audio = { filePath: audio.filePath, duration: audio.duration }
    if (guide.step) {
      result.answerKey = guide.step.key
      journey.profile[guide.step.key] = { value: audio || payload.skip ? '' : text, declined: !!payload.skip, updatedAt: Date.now() }
      if (audio) journey.profile[guide.step.key].voiceMessageId = result.id
    }
    state.messages.push(result)
    if (guide.step) appendGuide(state, journey)
  })
  return result
}
function saveProfile(fields) {
  if (!fields || typeof fields !== 'object') fail('INVALID_PROFILE', '建档信息格式不正确。')
  const validated = {}
  PROFILE_FIELDS.forEach(field => {
    const entry = fields[field.key]
    if (!entry || typeof entry.value !== 'string' || typeof entry.declined !== 'boolean') fail('INVALID_PROFILE', '请检查' + field.label + '。')
    const value = entry.declined ? '' : entry.value.trim()
    if (value.length > field.maxLength) fail('INVALID_PROFILE', field.label + '内容过长，请适当缩短。')
    if (field.key === 'lossType' && !LOSS_TYPES.some(t => t.value === value)) fail('INVALID_PROFILE', '请选择有效的离别类型。')
    validated[field.key] = { value, declined: entry.declined }
    if (!value && !entry.declined && entry.voiceMessageId) {
      const state = storage.read(), journey = currentJourney(state)
      if (!state.messages.some(message => message.id === entry.voiceMessageId && message.type === 'voice' && message.journeyId === journey.id && message.ownerId === state.guest.id)) fail('INVALID_PROFILE', '没有找到这项语音回答。')
      validated[field.key].voiceMessageId = entry.voiceMessageId
    }
  })
  storage.update(state => {
    const journey = currentJourney(state)
    PROFILE_FIELDS.forEach(field => {
      const next = validated[field.key], previous = journey.profile[field.key]
      if (previous.value !== next.value || previous.declined !== next.declined || previous.voiceMessageId !== next.voiceMessageId) journey.profile[field.key] = Object.assign(next, { updatedAt: Date.now() })
    })
  })
  return getProfile()
}
function listCollection(kind) {
  if (kind === 'letters') return [AUTHOR_LETTER]
  const state = storage.read()
  if (!state.consent || !state.guest) return []
  const journey = currentJourney(state)
  const source = kind === 'memories' ? state.memories : state.assets
  return source.filter(item => item.ownerId === state.guest.id && item.journeyId === journey.id && (kind === 'memories' || item.kind === kind))
}
module.exports = { acceptConsent, getOverview, markLetterRead, listMessages, saveMessage, getProfile, saveProfile, listCollection, startChat, getChatState, sendChatMessage }
