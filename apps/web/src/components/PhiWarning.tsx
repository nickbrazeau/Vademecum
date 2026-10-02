/**
 * The no-PHI rule, said where the typing happens rather than in a settings
 * page nobody opens. Nothing tries to detect identifiers: a detector that is
 * wrong in either direction is worse than a clear rule (ADR 0002, rule 4).
 */
export function PhiWarning() {
  return (
    <p className="phi-warning" role="note">
      <strong>No patient identifiers or HIPAA material.</strong>
    </p>
  )
}
