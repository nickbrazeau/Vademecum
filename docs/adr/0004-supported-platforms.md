# ADR 0004 — Supported platforms for the first pass

- Status: accepted
- Date: 2026-08-29
- Related: [0002](0002-local-first-boundary.md)

## Context

Vademecum is single-user. Supporting a narrow, named set of platforms is what makes a mobile-first
PWA affordable to build and honest to test. Naming them also tells the next slice which browser
features may be assumed without a fallback.

## Decision

**Supported for the first pass**

| Target | Version | Role |
| --- | --- | --- |
| macOS | 14 Sonoma or later | Host for the backend; primary desktop use |
| Safari (macOS) | 17+ | Primary desktop browser |
| Chrome (macOS) | last two stable releases | Secondary desktop browser |
| Safari (iOS/iPadOS) | 17+, iPhone 12 or later | Primary mobile target, including installed to Home Screen |

Everything else — Firefox, Android, Windows, Linux, older Safari — is **unsupported, not blocked**.
No user-agent sniffing, no interstitial. It will probably work; it is not tested and not fixed on
report.

**Baseline this permits us to assume, with no polyfill and no fallback:**

ES2022, CSS nesting, `:has()`, container queries, `dialog`, `structuredClone`, `AbortController`,
CSS `env(safe-area-inset-*)`, Service Worker + Cache API.

**Deliberately not assumed, because iOS Safari does not have them or gates them:**

- **Web Push and background sync** — absent or unreliable in installed iOS PWAs. Nothing in the
  product depends on a notification.
- **`beforeinstallprompt`** — Chrome-only. Installation is the OS "Add to Home Screen" flow; no
  in-app install button.
- **Web Speech / `SpeechRecognition`** — the archive used voice capture "where the browser supports
  it". iOS Safari does not expose it usefully. Voice capture is out of scope for this slice;
  keyboard and touch capture must be fast enough to stand alone.
- **Persistent storage guarantees** — Safari evicts unused origin storage. Browser storage therefore
  holds only regenerable local drafts. The SQLite database on the Mac is the canonical copy, always.

**Reaching it from the iPhone.** The backend binds `127.0.0.1` and refuses every other address
([ADR 0002](0002-local-first-boundary.md)). Phone access therefore requires a private tunnel the
owner sets up deliberately, forwarding to `127.0.0.1` on the Mac; there is no setting that opens the
bind up instead. It is not configured here and is not part of this slice; the mobile-first layout is
still built and verified at a 375 px viewport.

**Minimum viewport: 320 px.** Touch targets at least 44×44 px. Body text at least 16 px, so iOS
Safari does not zoom on focus.

## Consequences

- One CSS baseline, no vendor-prefix layer, no polyfill bundle.
- No feature is allowed to be reachable only through a capability iOS Safari lacks.
- Current automated acceptance uses installed Chrome at 1280 px and 390 px with isolated data.
  This checks a narrow layout, not an actual iPhone. Safari desktop and on-device iPhone testing
  remain separate human acceptance steps; do not describe Chrome viewport tests as Safari QA.
