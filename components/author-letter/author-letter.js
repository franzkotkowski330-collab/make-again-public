const { AUTHOR_LETTER } = require('../../utils/constants')

// Native adaptation of D:/my-ui-library/信封打开/letter-opening-demo/component.js.
Component({
  properties: { first: Boolean, saving: Boolean },
  data: {
    letter: AUTHOR_LETTER, opening: false, reading: false, closing: false,
    stars: Array.from({ length: 36 }, (_, i) => ({ id: i, style: `left:${(i * 71 + 17) % 97}%;top:${(i * 43 + 13) % 94}%;--dur:${2.1 + (i % 7) * .62}s;animation-delay:-${i * .41}s;` })),
    shards: Array.from({ length: 12 }, (_, i) => {
      const a = i * Math.PI / 6, b = (i + 1) * Math.PI / 6
      return { id: i, style: `clip-path:polygon(50% 50%,${50 + 52 * Math.cos(a)}% ${50 + 52 * Math.sin(a)}%,${50 + 52 * Math.cos(b)}% ${50 + 52 * Math.sin(b)}%);--angle:${i * 30}deg;--dist:${66 + i % 4 * 12}px;--twist:${(i % 2 ? 1 : -1) * (140 + i * 29)}deg;animation-delay:${.55 + i * .015}s;` }
    }),
    petals: Array.from({ length: 27 }, (_, i) => {
      const angle = i * 137.508 * Math.PI / 180, distance = 140 + i % 6 * 27
      return { id: i, style: `--dx:${Math.cos(angle) * distance}px;--dy:${Math.sin(angle) * distance - 70}px;--rot:${i * 67 % 360}deg;animation-delay:${1.6 + i % 7 * .055}s;` }
    })
  },
  lifetimes: {
    detached() { this.clearTimers() }
  },
  pageLifetimes: {
    hide() { this._hidden = true; this.clearTimers() },
    show() {
      if (!this._hidden) return
      this._hidden = false
      if (this.data.closing) { this.setData({ closing: false }); this.triggerEvent('cancel') }
      if (this.data.opening && !this.data.reading) this.setData({ reading: true })
    }
  },
  methods: {
    clearTimers() { clearTimeout(this._readTimer); clearTimeout(this._closeTimer) },
    openLetter() {
      if (this.data.opening) return
      this.setData({ opening: true })
      this._readTimer = setTimeout(() => this.setData({ reading: true }), 6250)
    },
    begin() { if (!this.data.saving && !this.data.closing) this.triggerEvent('begin') },
    archive() {
      this.setData({ closing: true })
      this._closeTimer = setTimeout(() => this.triggerEvent('archive'), 780)
    },
    resetClosing() { clearTimeout(this._closeTimer); this.setData({ closing: false }) },
    goBack() { this.triggerEvent('back') },
    preventTouchMove() {}
  }
})
