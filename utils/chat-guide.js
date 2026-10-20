const { PROFILE_FIELDS } = require('./constants')
const GREETING = '你好呀，很高兴在这里遇见你。\n你可以和我分享想法、情绪，或生活里的小事。我们先慢慢认识一下吧。'
const STEPS = [
  { key: 'nickname', title: '你的称呼', prompt: '你希望我怎么称呼你？\n一个喜欢的昵称就好，不需要真实姓名。', options: [], placeholder: '告诉我一个你喜欢的称呼…' },
  { key: 'lifeStage', title: '生活阶段', prompt: '最近的你，正处在怎样的生活阶段？', options: ['还在读书', '已经工作', '正在寻找方向', '暂时休息一下'], placeholder: '说说你目前的生活状态…' },
  { key: 'concern', title: '此刻的你', prompt: '最近，你最在意的一件事是什么？\n可以只说一点点，也可以慢慢说。', options: ['学业 / 工作压力', '人际关系', '一段离别', '最近有点迷茫'], placeholder: '最近，有什么事放在心上？' },
  { key: 'expectation', title: '陪伴期待', prompt: '在这里，你希望得到怎样的陪伴？\n也可以告诉我，有哪些话题暂时不想聊。', options: ['先听我说说', '陪我梳理情绪', '一起寻找方向'], placeholder: '你希望我们怎样相处？' }
]
function answered(entry) { return !!(entry && (entry.value || entry.declined || entry.voiceMessageId)) }
function getGuide(profile) {
  const index = STEPS.findIndex(step => !answered(profile[step.key]))
  const step = index < 0 ? null : STEPS[index]
  return { step, completed: STEPS.filter(item => answered(profile[item.key])).length, total: STEPS.length,
    maxLength: step ? PROFILE_FIELDS.find(field => field.key === step.key).maxLength : 2000 }
}
function question(step, profile) {
  const nickname = profile.nickname.value
  return step.key === 'lifeStage' && nickname ? '好的，' + nickname + '，很高兴认识你。\n' + step.prompt : step.prompt
}
module.exports = { GREETING, STEPS, getGuide, question }
