const path = require('node:path')
const fs = require('node:fs')
const automator = require('miniprogram-automator')
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
// Keep screenshots outside the project so the IDE file watcher does not reload
// the simulator between animation frames.
const output = path.resolve(process.env.MAKE_AGAIN_ARTIFACT_DIR || path.join(require('node:os').tmpdir(), 'make-again-ui-previews'))
;(async () => {
  fs.mkdirSync(output, { recursive: true })
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    await mini.switchTab('/pages/home/home')
    await mini.callWxMethod('navigateTo', { url: '/pages/letter/letter?first=1' })
    await wait(750)
    await mini.screenshot({ path: path.join(output, 'light-envelope.png') })
    await wait(6500)
    await mini.screenshot({ path: path.join(output, 'light-letter.png') })
    await mini.navigateBack()
    console.log('Light envelope and letter captured; original page restored.')
  } finally { mini.disconnect() }
})().catch(error => { console.error(error); process.exitCode = 1 })
