# 项目协作约定

## 每轮修改完成后必须生成 HTML 预览

- 完成“每一轮”修改（哪怕只动了原生小程序代码）都要重新生成 HTML 预览文件，供用户直接打开：
  - `node scripts/build-html-preview.cjs "Make-Again-预览.html"`
  - `node scripts/build-html-preview.cjs "Make-Again-登录页预览.html"`（同内容的登录页命名副本，保持两个文件同步）
- 可以用 `start "" "Make-Again-预览.html"` 在浏览器中打开给用户看。
- 生成前若改了原生页面/组件（WXML/WXSS/JS），直接重跑构建即可，脚本会自动转换并内嵌字体。
- 核验命令（能跑则跑）：`node scripts/check-project.cjs`、`node --test tests/*.test.cjs`、`node scripts/verify-html-preview.cjs`、原生 `node scripts/verify-login-native.cjs`（需开发者工具自动化端口 9420 可用；卡死时用 `cli.bat auto --project "D:\商赛\make again 手搓" --auto-port 9420` 重启自动化会话）。
- 每轮修改的经验记录在 `docs/登录页调整核验.md` 顶部追加新的小节。
