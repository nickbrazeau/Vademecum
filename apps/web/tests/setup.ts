import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

// jsdom's <dialog> support varies by version; the component has a fallback for
// exactly that, and pinning it here keeps these tests about the product.
beforeEach(() => {
  if (typeof HTMLDialogElement !== 'undefined') {
    HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
    HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
      this.removeAttribute('open')
    }
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  // Source-level suites run in the node environment and have no document.
  if (typeof window === 'undefined') return
  cleanup()
  // Each test starts at the app root; useRoute reads the real history.
  window.history.pushState({}, '', '/')
  window.localStorage.clear()
})
