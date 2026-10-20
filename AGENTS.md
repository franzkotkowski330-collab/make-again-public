# 项目协作约定

## 长期开发约束（用户确认，2026-10-16）

1. 本项目是微信小程序。所有功能、页面和交互必须使用微信小程序兼容的格式与能力实现，以 WXML、WXSS、JavaScript、JSON 和微信小程序 API 为开发基础，确保后续可以在微信开发者工具和真机中正常使用。HTML 文件仅用于预览，不能替代小程序实现；不得将浏览器专用 API 或仅适用于网页的代码直接用于小程序运行代码。
2. 所有现有和新增页面均采用登录页的背景，统一复用登录页对应的公共背景样式，不另行使用其他页面背景。后续调整背景时，以登录页为统一标准并保持各页面同步。
3. 默认优先使用微信原生组件和原生能力开发。除非用户特别提出，否则不主动引入新的第三方 UI 组件库，也不使用网页方案替代原生页面；需要自定义的交互仍须遵守微信小程序兼容性要求。

## 每轮修改完成后必须生成 HTML 预览

- 完成“每一轮”修改（哪怕只动了原生小程序代码）都要重新生成 HTML 预览文件，供用户直接打开：
  - `node scripts/build-html-preview.cjs "Make-Again-预览.html"`
  - `node scripts/build-html-preview.cjs "Make-Again-登录页预览.html"`（同内容的登录页命名副本，保持两个文件同步）
- 可以用 `start "" "Make-Again-预览.html"` 在浏览器中打开给用户看。
- 生成前若改了原生页面/组件（WXML/WXSS/JS），直接重跑构建即可，脚本会自动转换并内嵌字体。
- 核验命令（能跑则跑）：`node scripts/check-project.cjs`、`node --test tests/*.test.cjs`、`node scripts/verify-html-preview.cjs`、原生 `node scripts/verify-login-native.cjs`（需开发者工具自动化端口 9420 可用；卡死时用 `cli.bat auto --project "D:\商赛\make again 手搓" --auto-port 9420` 重启自动化会话）。
- 每轮修改的经验记录在 `docs/登录页调整核验.md` 顶部追加新的小节。
