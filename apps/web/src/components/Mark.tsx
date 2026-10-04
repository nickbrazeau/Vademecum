/**
 * The Vademecum mark: a pocket handbook, open, with its ribbon. Inline, so it
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
      <path d="M92 150 C150 126 212 136 256 170 L256 400 C212 366 150 356 92 380 Z" fill="var(--paper)" />
      <path d="M420 150 C362 126 300 136 256 170 L256 400 C300 366 362 356 420 380 Z" fill="var(--paper)" />
      <path
        d="M136 226 C176 212 208 216 232 232 M136 276 C176 262 208 266 232 282 M136 326 C176 312 208 316 232 332 M376 226 C336 212 304 216 280 232 M376 276 C336 262 304 266 280 282 M376 326 C336 312 304 316 280 332"
        fill="none"
        stroke="var(--accent)"
        strokeOpacity="0.45"
        strokeWidth="14"
        strokeLinecap="round"
      />
      <path d="M256 170 L256 400" stroke="var(--accent)" strokeWidth="16" strokeLinecap="round" />
      <path d="M236 96 H276 V262 L256 244 L236 262 Z" fill="#e0b877" />
    </svg>
  )
}
