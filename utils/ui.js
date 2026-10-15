function showError(error) { wx.showToast({ title: error.message || '暂时未能完成，请重试。', icon: 'none', duration: 3500 }) }
function formatTime(ms) { const d = new Date(ms); return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') }
module.exports = { showError, formatTime }
