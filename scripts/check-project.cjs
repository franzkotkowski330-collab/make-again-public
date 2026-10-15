const fs = require('node:fs')
const path = require('node:path')
const cp = require('node:child_process')
const root = path.resolve(__dirname, '..')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
for (const page of app.pages) {
  for (const ext of ['js', 'json', 'wxml', 'wxss']) {
    const file = path.join(root, page + '.' + ext)
    if (!fs.existsSync(file)) throw new Error('Missing page file: ' + file)
    if (ext === 'json') JSON.parse(fs.readFileSync(file, 'utf8'))
    if (ext === 'js') cp.execFileSync(process.execPath, ['--check', file])
  }
}
for (const item of app.tabBar.list) {
  if (!app.pages.includes(item.pagePath)) throw new Error('Unregistered tab: ' + item.pagePath)
  for (const key of ['iconPath', 'selectedIconPath']) {
    const file = path.join(root, item[key])
    if (!fs.existsSync(file)) throw new Error('Missing icon: ' + file)
    if (fs.statSync(file).size > 40000) throw new Error('Tab icon exceeds 40 KB: ' + file)
  }
}
for (const file of fs.readdirSync(path.join(root, 'utils')).filter(file => file.endsWith('.js'))) cp.execFileSync(process.execPath, ['--check', path.join(root, 'utils', file)])
for (const component of fs.readdirSync(path.join(root, 'components'))) {
  const prefix = path.join(root, 'components', component, component)
  for (const ext of ['js', 'json', 'wxml', 'wxss']) {
    if (!fs.existsSync(prefix + '.' + ext)) throw new Error('Missing component file: ' + prefix + '.' + ext)
  }
  JSON.parse(fs.readFileSync(prefix + '.json', 'utf8'))
  cp.execFileSync(process.execPath, ['--check', prefix + '.js'])
}
const config = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
if (config.appid !== 'wx0872b60ff03a1adc') throw new Error('AppID changed unexpectedly')
console.log('Project structure, page JSON, JavaScript syntax, native tab icons and AppID checked.')
