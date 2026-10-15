const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')
const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
const css = value => value.replace(/^@import[^;]+;\s*/gm, '').replace(/(-?\d*\.?\d+)rpx\b/g, (_, number) => `calc(var(--ma-rpx) * ${number})`)
let component
global.Component = definition => { component = definition }
require('../components/author-letter/author-letter')
delete global.Component
const data = component.data
let letter = read('components/author-letter/author-letter.wxml')
letter = letter.replace(/class="letter-overlay[^"\n]*"/, 'class="letter-overlay ml-root" id="letterOverlay" role="dialog" aria-modal="true" aria-label="主创的信"')
for (const [collection, tag, className] of [['stars', 'view', 'ml-star'], ['shards', 'view', 'ml-seal-shard'], ['petals', 'view', 'ml-burst-piece']]) {
  const expression = new RegExp(`<${tag} wx:for="\\{\\{${collection}\\}\\}"[^>]*><\\/${tag}>`)
  letter = letter.replace(expression, data[collection].map(item => `<div class="${className}" style="${escape(item.style)}" aria-hidden="true"></div>`).join(''))
}
letter = letter.replace(/<text wx:for="\{\{letter.paragraphs\}\}"[^>]*>\{\{item\}\}<\/text>/, data.letter.paragraphs.map(text => `<p class="letter-paragraph">${escape(text)}</p>`).join('\n'))
letter = letter.replace(/wx:if="\{\{!reading\}\}"/g, 'data-animation-only')
letter = letter.replace(/<view wx:if="\{\{reading\}\}" class="letter-paper">/, '<div class="letter-paper" id="letterPaper" hidden>')
letter = letter.replace(/class="letter-scroll"/, 'class="letter-scroll" tabindex="0" aria-label="信件正文"')
letter = letter.replace(/class="continue-letter"/, 'class="continue-letter" id="beginButton"')
letter = letter.replace(/class="back-letter"/, 'class="back-letter" id="backButton"')
letter = letter.replace(/bindtap="([^"]+)"/g, 'data-letter-action="$1"')
letter = letter.replace(/\s+(?:wx:if|wx:else|wx:key|catchtouchmove|disabled|loading|show-scrollbar)="[^"]*"/g, '')
letter = letter.replace(/\s+(?:scroll-y|enhanced|selectable)\b/g, '')
letter = letter.replace(/\s+wx:else\b/g, '')
letter = letter.replace(/<\/?view\b/g, tag => tag.startsWith('</') ? '</div' : '<div').replace(/<\/?text\b/g, tag => tag.startsWith('</') ? '</span' : '<span').replace(/<\/?scroll-view\b/g, tag => tag.startsWith('</') ? '</div' : '<div')
if (/\{\{|wx:/.test(letter)) throw new Error('Unresolved mini-program binding: ' + letter.match(/.{0,50}(?:\{\{|wx:).{0,90}/)[0])
// Build the preview login directly from the native WXML, including its only entry button.
let login = read('pages/login/login.wxml')
login = login.replace('class="screen login-screen"', 'class="screen login-screen ma-splash" id="splash" aria-label="登录页"')
login = login.replace(/\{\{animate \? 'brand-animate' : ''\}\}/g, 'brand-animate').replace(/\{\{animate \? 'motto-animate' : ''\}\}/g, 'motto-animate')
login = login.replace('wx:if="{{loginError}}"', 'id="loginError" hidden').replace('{{loginError}}', '')
login = login.replace('bindtap="loginWithWechat"', 'id="loginWechatButton" type="button"').replace(/\s+(?:loading|disabled|hover-class)="[^"]*"/g, '')
login = login.replace("{{isLoggingIn ? '正在登录' : '微信登录'}}", '微信登录')
login = login.replace(/<\/?view\b/g, tag => tag.startsWith('</') ? '</div' : '<div').replace(/<\/?text\b/g, tag => tag.startsWith('</') ? '</span' : '<span')
if (/\{\{|wx:|bindtap/.test(login)) throw new Error('Unresolved login binding')
// Keep the spotlight invitation's copy, layout and animation shared with the native page.
let chatGuide = read('pages/collection/collection.wxml').match(/<!-- CHAT_GUIDE_START -->([\s\S]*?)<!-- CHAT_GUIDE_END -->/)[1]
chatGuide = chatGuide.replace('bindtap="startChat"', 'id="chatGuideButton" type="button" data-page="chat"').replace(/\s+(?:loading|disabled)="[^"]*"/g, '')
chatGuide = chatGuide.replace(/<\/?view\b/g, tag => tag.startsWith('</') ? '</div' : '<div').replace(/<\/?text\b/g, tag => tag.startsWith('</') ? '</span' : '<span')
if (/\{\{|wx:|bindtap/.test(chatGuide)) throw new Error('Unresolved chat guide binding')
const styles = [read('assets/fonts/brand.wxss'), read('assets/fonts/motto.wxss'), css(read('components/author-letter/author-letter.wxss')), css(read('pages/login/login.wxss')), css(read('pages/collection/collection.wxss'))].join('\n')
const html = read('scripts/preview-template.html').replace('/* EMBEDDED_STYLES */', () => styles).replace('<!-- LETTER_TEMPLATE -->', () => letter).replace('<!-- LOGIN_TEMPLATE -->', () => login).replace('<!-- CHAT_GUIDE_TEMPLATE -->', () => chatGuide)
  .replace(/<span class="ma-icon" data-tab-icon="([a-z]+)"><\/span>/g, (_, name) => {
    const icon = suffix => fs.readFileSync(path.join(root, 'assets', `tab-${name}${suffix}.png`)).toString('base64')
    return `<span class="ma-icon ma-tab-icon" aria-hidden="true"><img class="ma-tab-default" alt="" src="data:image/png;base64,${icon('')}"><img class="ma-tab-selected" alt="" src="data:image/png;base64,${icon('-active')}"></span>`
  })
const target = path.join(root, process.argv[2] || 'Make-Again-预览.html')
fs.writeFileSync(target, html)
console.log('Created:', target, '(' + fs.statSync(target).size + ' bytes; fonts and animation embedded)')
