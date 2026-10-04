/**
 * The Vademecum mark: an open book with a torch over its spine, the flame of knowledge. Inline, so it
 * draws inside a conversation too, where the app has no address for an image.
 * The same drawing is the favicon and the home-screen icon (public/icon.svg).
 */

export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg
      className="mark"
      width={size}
      height={size}
      viewBox="0 0 512 512"
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="512" height="512" rx="112" fill="var(--accent)" />
      <path d="M72 300 C138 274 206 282 256 314 L256 438 C206 406 138 398 72 424 Z" fill="var(--paper)" />
      <path d="M440 300 C374 274 306 282 256 314 L256 438 C306 406 374 398 440 424 Z" fill="var(--paper)" />
      <path d="M110 338 C150 326 186 330 220 344 M110 372 C150 360 186 364 220 378" fill="none" stroke="var(--accent)" strokeOpacity="0.4" strokeWidth="12" strokeLinecap="round" />
      <path d="M402 338 C362 326 326 330 292 344 M402 372 C362 360 326 364 292 378" fill="none" stroke="var(--accent)" strokeOpacity="0.4" strokeWidth="12" strokeLinecap="round" />
      <path d="M243 206 H269 L264 436 H248 Z" fill="#b8863f" />
      <path d="M222 184 H290 L280 214 H232 Z" fill="#d9a54d" />
      <path d="M256 60 C298 104 322 136 308 170 C300 188 282 196 256 198 C230 196 212 188 204 170 C190 136 214 104 256 60 Z" fill="#e8873a" />
      <path d="M256 112 C278 136 288 154 281 172 C276 184 267 190 256 190 C245 190 236 184 231 172 C224 154 234 136 256 112 Z" fill="#f6d27a" />
    </svg>
  )
}
