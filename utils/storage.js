const STORAGE_KEY = 'make_again_local_v1'
function initialState() {
  return { schemaVersion: 1, letterRead: false, consent: null, guest: null, currentJourneyId: null, journeys: [], messages: [], assets: [], memories: [], letters: [] }
}
function storageError(code, message) { return Object.assign(new Error(message), { code }) }
function read() {
  let saved
  try { saved = wx.getStorageSync(STORAGE_KEY) }
  catch (_) { throw storageError('STORAGE_READ_FAILED', '暂时无法读取本地记录，请稍后重试。') }
  if (saved === '' || saved === undefined || saved === null) return initialState()
  if (typeof saved !== 'object' || saved.schemaVersion !== 1 ||
      !['journeys', 'messages', 'assets', 'memories', 'letters'].every(key => Array.isArray(saved[key])) ||
      (saved.guest && (!saved.consent || !saved.journeys.some(j => j.id === saved.currentJourneyId && j.ownerId === saved.guest.id)))) {
    throw storageError('STORAGE_INVALID', '本地记录格式异常。为保护已有内容，已停止写入，请勿清除缓存。')
  }
  return saved
}
function write(state) {
  try { wx.setStorageSync(STORAGE_KEY, state) }
  catch (_) { throw storageError('STORAGE_WRITE_FAILED', '本地保存失败，内容尚未保存。请检查设备存储空间后重试。') }
  return state
}
function update(mutator) { const state = read(); mutator(state); return write(state) }
module.exports = { STORAGE_KEY, read, update }
