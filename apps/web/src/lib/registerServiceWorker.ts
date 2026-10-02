/**
 * Registering the worker is optional: the app works without it, and offline
 * support is the only thing it buys. Failure here is never shown to the owner.
 */

export function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return
  if (!import.meta.env.PROD) return
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js', { type: 'module', scope: '/' }).catch(() => {
      /* no worker, no offline shell, no message worth interrupting for */
    })
  })
}
