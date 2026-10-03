/**
 * The model connection.
 *
 * This page explains, and lets you change, one thing: whether Codex on this Mac
 * is signed in to ChatGPT. It sends no prompt itself; the two places that do
 * are Build learning material in Sources and Grade in Tutor, and both disclose
 * what they will send before they send it.
 *
 * Three rules shape the sign-in flow:
 *
 * 1. **The one-time code lives in component state and nowhere else.** Not
 *    localStorage, not sessionStorage, not the service worker cache. Closing
 *    the page loses it, which is correct: it is single-use and short-lived, and
 *    the backend can cancel the sign-in without the browser holding anything.
 * 2. **Nothing opens a browser for you.** The verification link is a link. An
 *    application that launches an external browser at a URL it received over a
 *    pipe is doing something you did not ask for.
 * 3. **A failure is named, not smoothed over.** Every state below says what is
 *    actually true: checking sign-in and usage goes through Codex, which
 *    contacts OpenAI to answer it, and carries no learning content with it.
 */

import { useCallback, useEffect, useState } from 'react'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { PLAN_LABEL } from '../lib/types'
import type { DeviceLogin, ModelState, ModelStatus, UsageWindow } from '../lib/types'
import { useLoad } from '../lib/useLoad'

const STATE_LABEL: Record<ModelState, string> = {
  connecting: 'Checking…',
  signed_out: 'Not signed in',
  signed_in: 'Signed in',
  rate_limited: 'Usage limit reached',
  unavailable: 'Unavailable'
}

export function planLabel(plan: string | null): string | null {
  if (!plan) return null
  return PLAN_LABEL[plan] ?? plan
}

/** A window duration in the words a person would use for it. */
export function windowLabel(minutes: number | null): string {
  if (minutes === null || minutes <= 0) return 'Usage'
  if (minutes % 10080 === 0) {
    const weeks = minutes / 10080
    return weeks === 1 ? 'This week' : `Every ${weeks} weeks`
  }
  if (minutes % 1440 === 0) {
    const days = minutes / 1440
    return days === 1 ? 'Today' : `Every ${days} days`
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60
    return hours === 1 ? 'This hour' : `Every ${hours} hours`
  }
  return `Every ${minutes} minutes`
}

/** A reset time in local words, or nothing if the backend could not read one. */
export function resetLabel(isoTime: string | null): string | null {
  if (!isoTime) return null
  const when = new Date(isoTime)
  if (Number.isNaN(when.getTime())) return null
  return when.toLocaleString(undefined, {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit'
  })
}

/**
 * Only an https link is rendered as a link.
 *
 * The URL arrives over the bridge. It is very unlikely to be anything but the
 * real verification page, and "very unlikely" is not the standard for what
 * becomes an `href`.
 */
export function isSafeExternalUrl(url: string): boolean {
  try {
    return new URL(url).protocol === 'https:'
  } catch {
    return false
  }
}

function Usage({ label, window: usage }: { label: string; window: UsageWindow }) {
  const resets = resetLabel(usage.resets_at)
  return (
    <li>
      <span className="title">{windowLabel(usage.window_minutes) || label}</span>
      <p className="body">
        {usage.used_percent}% used
        {resets ? <> · resets {resets}</> : null}
      </p>
      {/* Decoration. The number above it is the accessible fact. */}
      <div className="usage-bar" aria-hidden="true">
        <div className="usage-fill" style={{ width: `${usage.used_percent}%` }} />
      </div>
    </li>
  )
}

function SignInPanel({
  login,
  onCancel,
  onDone,
  busy
}: {
  login: DeviceLogin
  onCancel: () => void
  onDone: () => void
  busy: boolean
}) {
  return (
    <section className="card signin" aria-labelledby="signin-heading">
      <h3 id="signin-heading">Finish signing in</h3>
      <ol className="steps">
        <li>
          Open{' '}
          {isSafeExternalUrl(login.verification_url) ? (
            <a href={login.verification_url} target="_blank" rel="noreferrer noopener">
              {login.verification_url}
            </a>
          ) : (
            <code>{login.verification_url}</code>
          )}{' '}
          in your browser. Nothing opens it for you.
        </li>
        <li>
          Enter this one-time code:{' '}
          <code className="device-code" data-testid="device-code">
            {login.user_code}
          </code>
        </li>
        <li>Come back here and check again.</li>
      </ol>
      <p className="muted small">
        This code is shown once and is not saved anywhere in this browser. If you close this page,
        cancel and start again.
      </p>
      <div className="actions">
        <button type="button" className="button primary" onClick={onDone} disabled={busy}>
          I have finished — check again
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={busy}>
          Cancel sign-in
        </button>
      </div>
    </section>
  )
}

function WhatIsSent() {
  return (
    <section className="card" aria-labelledby="model-privacy-heading">
      <h3 id="model-privacy-heading">What this sends</h3>
      <dl>
        <dt>Right now</dt>
        <dd>
          <strong>No learning content from these connection controls.</strong> This page reads whether Codex is signed in and
          how much of your plan you have used, and can sign you in. Those operations run through
          Codex on this Mac, and Codex contacts OpenAI to answer them, so this page is not an
          offline page. Its sign-in and status controls do not send your notes or answers.
        </dd>

        <dt>Build and Grade</dt>
        <dd>
          These separate actions send the selected study context disclosed on their pages to OpenAI
          through your ChatGPT sign-in. Build includes source excerpts and follow-up evidence and
          question checks; Grade sends one question, its reference answer and rubric, and your answer.
          Weekly literature checks, if you enable them, send only short public topic words to PubMed
          while the app is running.
        </dd>

        <dt>Your sign-in</dt>
        <dd>
          Codex holds your ChatGPT credentials and makes the request. Vademecum never sees a token
          and <strong>no API key is used</strong> — model use draws on your ChatGPT plan and its
          usage limits.
        </dd>
      </dl>
    </section>
  )
}

export function Model() {
  const [online, setOnline] = useState(() => navigator.onLine)
  const { result, reload } = useLoad(() => api.modelStatus(), [])
  // Which connection this is (ADR 0019): Claude signs in in a terminal, never from here.
  const health = useLoad(() => api.health(), [])
  const claudeMode = health.result.state === 'ready' && health.result.value.model_mode === 'claude'
  // In component state, deliberately. See the note at the top of this file.
  const [login, setLogin] = useState<DeviceLogin | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])

  const act = useCallback(
    async (action: () => Promise<unknown>, keepLogin = false) => {
      setBusy(true)
      setFailure(null)
      try {
        await action()
        if (!keepLogin) setLogin(null)
      } catch (error) {
        setFailure(asApiError(error))
      } finally {
        setBusy(false)
      }
    },
    []
  )

  // A device code the connection has already accepted is stale, and a panel
  // inviting the owner to enter it again is a lie about what is left to do.
  // `rate_limited` counts as signed in: it is a plan state, not an auth one.
  // Every other state keeps the code, because a check that still says signed
  // out usually means the code has not been entered yet.
  const signedInNow =
    result.state === 'ready' &&
    (result.value.state === 'signed_in' || result.value.state === 'rate_limited')

  useEffect(() => {
    if (signedInNow) setLogin(null)
  }, [signedInNow])

  const signIn = () =>
    void act(async () => {
      setLogin(await api.startModelLogin())
    }, true)

  const cancel = () =>
    void act(async () => {
      await api.cancelModelLogin()
      reload()
    })

  const restart = () =>
    void act(async () => {
      await api.restartModel()
      reload()
    })

  const check = () =>
    void act(async () => {
      reload()
    }, true)

  // Being offline is a different fact from the backend being down, and it has
  // a different remedy. It is stated on both paths, because the fetch that
  // fails while offline lands in the branch below.
  const offlineNotice = online ? null : (
    <p className="warn" role="status">
      This device is offline. The connection to Codex cannot be checked from here; nothing saved on
      this Mac is affected.
    </p>
  )

  if (result.state === 'failed') {
    return (
      <div className="stack">
        {offlineNotice}
        <Unavailable error={result.error} onRetry={reload} />
        <WhatIsSent />
      </div>
    )
  }

  const status: ModelStatus | null = result.state === 'ready' ? result.value : null
  const state: ModelState = status?.state ?? 'connecting'
  const plan = planLabel(status?.plan ?? null)
  const limits = status?.rate_limits ?? null

  return (
    <div className="stack">
      <section className="card" aria-labelledby="model-heading">
        <h2 id="model-heading">Model connection</h2>

        {offlineNotice}

        <p className="state-line" role="status" aria-live="polite">
          <span className={`badge badge-${state}`}>{STATE_LABEL[state]}</span>
          {plan ? <span className="plan">{plan} plan</span> : null}
        </p>

        <p className="body">
          {status
            ? status.detail
            : claudeMode
              ? 'Checking whether the Claude Code CLI on this Mac is signed in. No note, question or answer is sent.'
              : 'Checking whether Codex on this Mac is signed in. That check goes through Codex, which contacts OpenAI to answer it; no note, question or answer is sent.'}
        </p>

        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}

        <div className="actions">
          {state === 'signed_out' && !status?.login_pending && login === null && !claudeMode ? (
            <button type="button" className="button primary" onClick={signIn} disabled={busy}>
              Sign in with ChatGPT
            </button>
          ) : null}
          {state === 'signed_out' && claudeMode ? (
            <span className="muted small">
              Sign in from a terminal: run <code>claude</code> once and follow its sign-in. This page cannot do it for you.
            </span>
          ) : null}
          <button type="button" className="button" onClick={check} disabled={busy}>
            Check again
          </button>
          <button type="button" className="button ghost" onClick={restart} disabled={busy}>
            Restart the connection
          </button>
        </div>

        {state === 'unavailable' ? (
          <p className="muted small">
            No note, question or answer was sent anywhere. Restarting stops the Codex process on
            this Mac and starts a fresh one; your notes and flags are untouched either way.
          </p>
        ) : null}
      </section>

      {login ? (
        <SignInPanel login={login} onCancel={cancel} onDone={check} busy={busy} />
      ) : status?.login_pending ? (
        <section className="card" aria-labelledby="pending-heading">
          <h3 id="pending-heading">A sign-in is waiting</h3>
          <p className="body">
            A sign-in was started and has not finished. The one-time code is not kept, so cancel it
            and start again if you no longer have it.
          </p>
          <div className="actions">
            <button type="button" className="button" onClick={cancel} disabled={busy}>
              Cancel sign-in
            </button>
          </div>
        </section>
      ) : null}

      {limits && (limits.primary || limits.secondary) ? (
        <section className="card" aria-labelledby="usage-heading">
          <h3 id="usage-heading">Plan usage</h3>
          {limits.limited ? (
            <p className="warn" role="status">
              A usage limit has been reached. Model actions will not work until it resets.
            </p>
          ) : null}
          <ul className="list">
            {limits.primary ? <Usage label="Primary" window={limits.primary} /> : null}
            {limits.secondary ? <Usage label="Secondary" window={limits.secondary} /> : null}
          </ul>
          <p className="muted small">
            Reported by Codex for your ChatGPT plan. Vademecum does not see an account name or
            address.
          </p>
        </section>
      ) : null}

      <WhatIsSent />
    </div>
  )
}
