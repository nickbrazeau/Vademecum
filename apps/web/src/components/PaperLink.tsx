/** Construct citation destinations from identifiers, never trust a model-supplied URL. */
export function PaperLink({ pmid, title }: { pmid: string; title?: string }) {
  if (!/^\d+$/.test(pmid)) return title ? <span>{title}</span> : null
  return <a href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`} target="_blank" rel="noopener noreferrer">
    {title || `Read on PubMed (PMID ${pmid})`}
  </a>
}
