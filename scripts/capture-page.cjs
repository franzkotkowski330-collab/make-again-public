const fs = require('node:fs')
const path = require('node:path')
const automator = require('miniprogram-automator')
const pageName = process.argv[2] || 'home'
const routes = { home: '/pages/home/home', chat: '/pages/chat/chat', collection: '/pages/collection/collection', profile: '/pages/profile/profile' }
if (!routes[pageName]) throw new Error('Expected home, chat, collection or profile')
;(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const current = await mini.currentPage()
    if (current.path === 'pages/letter/letter') await mini.navigateBack()
    let page = await mini.switchTab(routes[pageName])
    if (page.path === 'pages/letter/letter') { await mini.navigateBack(); page = await mini.currentPage() }
    if ('/' + page.path !== routes[pageName]) throw new Error('Refusing to label screenshot with the wrong route: ' + page.path)
    console.log('Capturing:', page.path)
    await mini.pageScrollTo(0)
    await page.waitFor(800)
    fs.mkdirSync(path.resolve(__dirname, '../artifacts/ui'), { recursive: true })
    await mini.screenshot({ path: path.resolve(__dirname, '../artifacts/ui/' + pageName + '.png') })
    console.log('Screenshot saved.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
