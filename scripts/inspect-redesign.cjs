const fs = require('node:fs'), path = require('node:path')
const automator = require('miniprogram-automator')
;(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    let page = await mini.currentPage()
    if (page.path !== 'pages/login/login') {
      await mini.navigateTo('/pages/login/login')
      page = await mini.currentPage()
    }
    if (page.path !== 'pages/login/login') throw new Error('Login screenshot route is not ready')
    await page.waitFor('.brand-name')
    console.log('Current route:', page.path)
    console.log('Page data keys:', Object.keys(await page.data()))
    const logo = await page.$('.brand-name')
    if (logo) console.log('Brand:', await logo.text(), 'font:', await logo.style('font-family'), 'size:', await logo.size())
    const motto = await page.$('.motto-text')
    if (motto) console.log('Motto:', await motto.text(), 'font:', await motto.style('font-family'), 'animation:', await motto.style('animation-name'), 'duration:', await motto.style('animation-duration'), 'easing:', await motto.style('animation-timing-function'))
    for (const selector of ['.wechat-login', '.guest-entry']) {
      const button = await (await page.$(selector)).$('button')
      console.log('Login entry:', await button.text(), 'bounds:', await button.offset(), await button.size())
    }
    await page.waitFor(2200)
    fs.mkdirSync(path.resolve(__dirname, '../artifacts/ui'), { recursive: true })
    await mini.screenshot({ path: path.resolve(__dirname, '../artifacts/ui/login.png') })
    console.log('Screenshot captured.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
