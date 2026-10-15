const a = require('miniprogram-automator')
;(async () => { const mini = await a.connect({ wsEndpoint:'ws://127.0.0.1:9420' }); try {
  let p = await mini.currentPage(); if(p.path !== 'pages/chat/chat') { await mini.switchTab('/pages/chat/chat'); p = await mini.currentPage() } console.log('Route:',p.path)
  console.log('Heights:',await p.data('chatHeight'),await p.data('keyboardHeight'))
  console.log('Window:',await mini.evaluate(function(){return wx.getWindowInfo()}))
  for(const selector of ['.chat-screen','.chat-top','.message-scroll','.input-dock','.chat-input','.send-button']){const e=await p.$(selector); if(e)console.log(selector,await e.offset(),await e.size())}
}finally{mini.disconnect()} })().catch(e=>{console.error(e);process.exitCode=1})
