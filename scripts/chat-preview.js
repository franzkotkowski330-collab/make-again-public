// The HTML preview runs the real native controller and local service against a small browser adapter.
(() => {
  const root = document.getElementById('chat'), template = document.getElementById('chatNativeTemplate')
  const app = { globalData: {} }, cache = {}, temporaryAudio = new Map()
  let page, renderQueued = false, scrollToEnd = false, pointerHeld = false, stream, mediaRecorder, startTime, recorderTimeout, toastTimer
  const db = new Promise((resolve, reject) => {
    const request = indexedDB.open('make-again-chat-preview-audio', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('audio')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  // A file: browser may deny IndexedDB. Text remains available, and voice saving reports the failure.
  db.catch(() => {})
  function audioStore(mode, action) {
    return db.then(database => new Promise((resolve, reject) => {
      const tx = database.transaction('audio', mode), request = action(tx.objectStore('audio'))
      tx.oncomplete = () => resolve(request.result)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    }))
  }
  function toast(title) {
    let node = document.getElementById('chatToast')
    if (!node) { node = document.createElement('div'); node.id = 'chatToast'; node.className = 'ma-preview-toast'; document.getElementById('phone').appendChild(node) }
    node.textContent = title; node.hidden = false
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true }, 3500)
  }
  const handlers = { Start: new Set(), Stop: new Set(), Error: new Set(), InterruptionBegin: new Set() }
  const notify = (event, value) => handlers[event].forEach(callback => callback(value))
  const stopStream = () => { if (stream) stream.getTracks().forEach(track => track.stop()); stream = null }
  const recorder = {
    start() {
      try {
        const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => MediaRecorder.isTypeSupported(type))
        mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {})
        const chunks = []
        mediaRecorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
        mediaRecorder.onerror = () => { stopStream(); notify('Error', {}) }
        mediaRecorder.onstop = () => {
          clearTimeout(recorderTimeout)
          const duration = Date.now() - startTime, blob = new Blob(chunks, { type: mediaRecorder.mimeType })
          const filePath = URL.createObjectURL(blob)
          temporaryAudio.set(filePath, blob); stopStream()
          notify('Stop', { tempFilePath: filePath, duration })
        }
        startTime = Date.now(); mediaRecorder.start(); notify('Start')
        recorderTimeout = setTimeout(() => recorder.stop(), 60000)
      } catch (_) { stopStream(); notify('Error', {}) }
    },
    stop() { if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop() }
  }
  for (const event of Object.keys(handlers)) {
    recorder['on' + event] = callback => handlers[event].add(callback)
    recorder['off' + event] = callback => handlers[event].delete(callback)
  }
  const wx = {
    getStorageSync(key) { const value = localStorage.getItem('preview_' + key); return value ? JSON.parse(value) : '' },
    setStorageSync(key, value) { localStorage.setItem('preview_' + key, JSON.stringify(value)) },
    showToast: options => toast(options.title), hideKeyboard() { if (document.activeElement.tagName === 'TEXTAREA') document.activeElement.blur() },
    switchTab() { window.dispatchEvent(new Event('ma-chat-home')) },
    navigateTo(options) {
      if (options.url.includes('privacy')) { toast('文字、语音与档案只保存在本机。录音需要麦克风权限；语音不自动转写，智能回复待开启。'); return }
      showProfile()
    },
    authorize(options) {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { options.fail(); return }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(value => {
        stream = value; options.success()
        if (!mediaRecorder || mediaRecorder.state === 'inactive') stopStream()
      }, () => options.fail())
    },
    showModal(options) { toast(options.content); if (options.success) options.success({ confirm: false }) },
    openSetting() { toast('请在浏览器地址栏的权限设置中允许使用麦克风，然后重试') },
    getRecorderManager: () => recorder,
    getFileSystemManager: () => ({
      saveFile(options) {
        const blob = temporaryAudio.get(options.tempFilePath)
        if (!blob) { options.fail(); return }
        const savedFilePath = 'ma-audio:' + Date.now() + '-' + Math.random().toString(36).slice(2)
        audioStore('readwrite', store => store.put(blob, savedFilePath)).then(() => options.success({ savedFilePath }), () => options.fail())
      }
    }),
    createInnerAudioContext() {
      const audio = new Audio(); let src, revision = 0, ownedUrl
      return {
        set src(value) { src = value },
        async play() {
          const token = ++revision
          try {
            let url = src
            if (src.startsWith('ma-audio:')) {
              const blob = await audioStore('readonly', store => store.get(src))
              if (!blob) throw new Error('Missing recording')
              if (token !== revision) return
              ownedUrl = URL.createObjectURL(blob); url = ownedUrl
            }
            if (token !== revision) return
            audio.src = url; await audio.play()
          } catch (_) { if (token === revision) audio.dispatchEvent(new Event('error')) }
        },
        stop() { revision++; audio.pause(); if (ownedUrl) { URL.revokeObjectURL(ownedUrl); ownedUrl = null } },
        destroy() { this.stop(); audio.src = '' },
        onEnded(callback) { audio.addEventListener('ended', callback) }, onError(callback) { audio.addEventListener('error', callback) }
      }
    }
  }
  function load(name, parent = '') {
    const parts = (name.startsWith('.') ? parent.split('/').slice(0, -1).join('/') + '/' + name : name).split('/')
    const clean = []; parts.forEach(part => { if (part === '..') clean.pop(); else if (part && part !== '.') clean.push(part) })
    const key = clean.join('/').replace(/\.js$/, '') + '.js'
    if (cache[key]) return cache[key].exports
    const module = { exports: {} }; cache[key] = module
    new Function('module', 'exports', 'require', 'wx', 'Page', 'getApp', window.__maChatSources[key])(module, module.exports, value => load(value, key), wx, definition => { page = definition }, () => app)
    return module.exports
  }
  load('pages/chat/chat.js')
  const service = load('utils/service.js'), constants = load('utils/constants.js')
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = patch => {
    Object.assign(page.data, patch)
    if (Object.hasOwn(patch, 'scrollTarget')) scrollToEnd = true
    if (!renderQueued) { renderQueued = true; queueMicrotask(render) }
  }
  const iconPaths = {
    'lock-on': '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
    'user-circle': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="9" r="3"/><path d="M6 19a6 6 0 0 1 12 0"/>',
    'chevron-right': '<path d="m9 5 7 7-7 7"/>',
    'user': '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2Z"/>',
    'microphone': '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
    'keyboard': '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h.1m4 0h.1m4 0h.1m4 0h.1M6 12h.1m4 0h.1m4 0h.1m4 0h.1M7 16h10"/>',
    'arrow-up': '<path d="M12 19V5m-6 6 6-6 6 6"/>',
    'play': '<path d="m8 4 12 8L8 20Z"/>', 'pause': '<path d="M8 5v14M16 5v14"/>'
  }
  const expressions = new Map()
  function expression(source, scope) {
    if (!expressions.has(source)) expressions.set(source, new Function('scope', 'with(scope){return (' + source + ')}'))
    return expressions.get(source)(scope)
  }
  function value(source, scope) {
    const exact = source.match(/^\{\{((?:(?!\{\{|\}\})[\s\S])*)\}\}$/)
    return exact ? expression(exact[1], scope) : source.replace(/\{\{([\s\S]*?)\}\}/g, (_, code) => expression(code, scope) ?? '')
  }
  function appendNodes(nodes, target, scope) {
    let condition = false
    for (const node of nodes) {
      if (node.nodeType === Node.TEXT_NODE) { target.appendChild(document.createTextNode(value(node.textContent, scope))); continue }
      if (node.nodeType !== Node.ELEMENT_NODE) continue
      if (node.hasAttribute('wx:if')) { condition = !!value(node.getAttribute('wx:if'), scope); if (!condition) continue }
      else if (node.hasAttribute('wx:else')) { if (condition) continue }
      if (node.hasAttribute('wx:for')) {
        const items = value(node.getAttribute('wx:for'), scope) || []
        items.forEach((item, index) => { const clone = node.cloneNode(true); clone.removeAttribute('wx:for'); clone.removeAttribute('wx:if'); appendNodes([clone], target, Object.assign({}, scope, { item, index })) })
        continue
      }
      const nativeTag = node.tagName.toLowerCase(), tag = ({ view: 'div', text: 'span', block: 'div', 'scroll-view': 'div' })[nativeTag] || nativeTag
      const element = document.createElement(tag)
      if (nativeTag === 'block') element.setAttribute('data-native-block', '')
      for (const attr of node.attributes) {
        if (attr.name.startsWith('wx:') || /^(scroll-|auto-height|show-confirm-bar|adjust-position|cursor-spacing)/.test(attr.name)) continue
        if (attr.name.startsWith('bind') || attr.name.startsWith('catch')) {
          const event = attr.name.replace(/^(bind|catch):?/, ''), method = attr.value
          if (event.startsWith('touch')) { element.dataset[event] = method; continue }
          if (event === 'keyboardheightchange') continue
          element.addEventListener(event === 'tap' ? 'click' : event, nativeEvent => {
            const source = nativeEvent.target
            page[method]({ detail: { value: source.value }, currentTarget: { dataset: element.dataset }, touches: [] })
          })
          continue
        }
        const resolved = value(attr.value, scope)
        if (attr.name === 'disabled') { if (resolved) element.disabled = true; continue }
        if (attr.name === 'style') element.style.cssText = String(resolved).replace(/(-?\d*\.?\d+)rpx\b/g, (_, n) => 'calc(var(--ma-rpx) * ' + n + ')')
        else if (attr.name === 'value' && tag === 'textarea') element.value = resolved
        else element.setAttribute(attr.name, resolved == null ? '' : resolved)
      }
      if (nativeTag === 't-icon') {
        const size = element.getAttribute('size') || '28rpx'
        element.style.width = element.style.height = size.replace(/([\d.]+)rpx/, 'calc(var(--ma-rpx) * $1)')
        element.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (iconPaths[element.getAttribute('name')] || '') + '</svg>'
      } else appendNodes([...node.childNodes], element, scope)
      if (tag === 'textarea') { element.setAttribute('aria-label', '聊天文字输入'); element.rows = 1 }
      target.appendChild(element)
    }
  }
  function render() {
    renderQueued = false
    const previous = root.querySelector('.message-scroll'), top = previous ? previous.scrollTop : 0
    const active = root.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA'
    const selection = active ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null
    const fragment = document.createDocumentFragment()
    appendNodes([...template.content.childNodes], fragment, page.data)
    root.replaceChildren(fragment)
    const scroll = root.querySelector('.message-scroll')
    if (scroll) scroll.scrollTop = scrollToEnd ? scroll.scrollHeight : top
    scrollToEnd = false
    if (active) {
      const input = root.querySelector('.chat-input')
      if (input) { input.focus({ preventScroll: true }); input.setSelectionRange(...selection) }
    }
  }
  root.addEventListener('pointerdown', event => {
    const target = event.target.closest('[data-touchstart]')
    if (!target || target.disabled) return
    event.preventDefault(); pointerHeld = true
    page[target.dataset.touchstart]({ touches: [{ clientY: event.clientY }] })
  })
  document.addEventListener('pointermove', event => { if (pointerHeld) page.moveRecording({ touches: [{ clientY: event.clientY }] }) })
  document.addEventListener('pointerup', () => { if (pointerHeld) { pointerHeld = false; page.finishRecording() } })
  document.addEventListener('pointercancel', () => { if (pointerHeld) { pointerHeld = false; page.cancelRecordGesture() } })
  document.addEventListener('visibilitychange', () => { if (document.hidden) { page.leave(); stopStream() } else if (!root.hidden) page.onShow() })
  window.addEventListener('pagehide', () => { page.leave(); stopStream() })
  function showProfile() {
    let panel = document.getElementById('chatProfileEditor')
    if (panel) panel.remove()
    panel = document.createElement('div'); panel.id = 'chatProfileEditor'; panel.className = 'ma-preview-profile'
    const title = document.createElement('h2'); title.textContent = '个人档案'; panel.appendChild(title)
    const note = document.createElement('p'); note.className = 'ma-description'; note.textContent = '由你决定分享多少。修改只保存在当前浏览器。'; panel.appendChild(note)
    const profile = service.getProfile(), inputs = []
    for (const field of constants.PROFILE_FIELDS) {
      const label = document.createElement('label'); label.textContent = field.label; panel.appendChild(label)
      const input = document.createElement(field.key === 'lossType' ? 'select' : 'textarea')
      if (field.key === 'lossType') constants.LOSS_TYPES.forEach(type => { const option = document.createElement('option'); option.value = type.value; option.textContent = type.label; input.appendChild(option) })
      else { input.maxLength = field.maxLength; input.rows = field.maxLength > 60 ? 3 : 1; input.placeholder = field.hint }
      input.value = profile[field.key].value; panel.appendChild(input)
      const refusal = document.createElement('label'), checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = profile[field.key].declined; refusal.append(checkbox, ' 暂不分享'); panel.appendChild(refusal)
      if (profile[field.key].voiceMessageId) { const voice = document.createElement('p'); voice.className = 'ma-description'; voice.textContent = '已通过语音回答，可回聊天中收听；填写文字可替换。'; panel.appendChild(voice) }
      inputs.push({ field, input, checkbox })
    }
    const save = document.createElement('button'); save.textContent = '保存档案并返回'
    save.onclick = () => {
      inputs.forEach(({ field, input, checkbox }) => { profile[field.key] = Object.assign({}, profile[field.key], { value: input.value, declined: checkbox.checked }) })
      try { service.saveProfile(profile); panel.remove(); page.refresh(true) } catch (error) { toast(error.message) }
    }
    const close = document.createElement('button'); close.textContent = '取消'; close.style.marginLeft = '12px'; close.onclick = () => panel.remove()
    panel.append(save, close); document.getElementById('appBody').appendChild(panel)
  }
  window.maChat = { show() { page.onShow() }, hide() { page.onHide(); document.getElementById('chatProfileEditor')?.remove() } }
  render()
})();
