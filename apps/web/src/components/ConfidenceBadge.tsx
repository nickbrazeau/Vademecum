/**
 * A confidence tier, always named as what it is.
 *
 * Low / Medium / High describe how much the owner trusts the material. They are
 * never a mastery score, a priority, a difficulty, or a claim that anything in
 * the material has been verified — so the badge carries those words with it
 * rather than leaving a bare colour to be read as a ranking.
 */

import { CONFIDENCE_MEANING, TIER_LABEL } from '../lib/types'
import type { Tier } from '../lib/types'

export function ConfidenceBadge({ tier, label }: { tier: Tier; label?: string }) {
  return (
    <span className={`tier tier-${tier}`} title={CONFIDENCE_MEANING}>
      <span className="visually-hidden">Tier confidence: </span>
      {label ?? TIER_LABEL[tier]}
    </span>
  )
}

/** The sentence itself, for the top of any section that sorts by tier. */
export function ConfidenceMeaning() {
  return (
    <p className="muted small">
      <strong>Tier confidence</strong> — {CONFIDENCE_MEANING}
    </p>
  )
}
