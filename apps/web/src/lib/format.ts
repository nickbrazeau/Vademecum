/** Small shared formatters. An unparseable value shows as itself, never as a guess. */

/** A date in local words, or the raw string if it cannot be read. */
export function dateLabel(value: string | null): string {
  if (!value) return 'not recorded'
  const when = new Date(value)
  if (Number.isNaN(when.getTime())) return value
  return when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** A date and time, for things that happen more than once a day. */
export function momentLabel(value: string | null): string {
  if (!value) return 'not yet'
  const when = new Date(value)
  if (Number.isNaN(when.getTime())) return value
  return when.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit'
  })
}

export function byteLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

