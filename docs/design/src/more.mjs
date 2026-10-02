// Sources, Improvement Map and Model artboards. Imported by build.mjs.
// Sample content is illustrative; nothing here is a real citation.

export function more({ page, phoneWrap, desktopWrap, column, headerPhone, headerDesktop, FOOTER, PHI, icon }) {
  // ---------- Sources: the intake surface, denser than the cover sheet ----------
  const DENSE = `
    .row { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 6px 0; border-bottom: 1px solid var(--line); }
    .row:last-child { border-bottom: none; }
    .row-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .row-title { font-weight: 600; font-size: 15px; line-height: 1.35; }
    .row-meta { font: 12px/1.4 var(--sans); color: var(--muted); }
    .mini-bar { width: 56px; height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; flex: none; }
    .mini-fill { height: 100%; background: var(--accent); }
    .file-row { display: flex; align-items: flex-start; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--line); }
    .file-row:last-child { border-bottom: none; }
    .file-icon { flex: none; width: 28px; height: 34px; border: 1px solid var(--line); border-radius: 4px; background: var(--paper); display: flex; align-items: flex-end; justify-content: center; font: 600 9px/1 var(--sans); color: var(--muted); padding-bottom: 4px; letter-spacing: 0.04em; }
    .button.small { min-height: 32px; font-size: 13px; padding: 0 10px; }
    .select { font: 13px/1 var(--sans); min-height: 32px; padding: 0 28px 0 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--card); display: inline-flex; align-items: center; position: relative; }
    .select svg { position: absolute; right: 8px; top: 8px; }
    .tier-option { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 0 12px; border: 1px solid var(--line); border-radius: 999px; font: 14px/1 var(--sans); }
    .tier-option.on { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
    .dot { width: 10px; height: 10px; border-radius: 999px; }
  `

  const chev = icon.chevron('#5c6470')
  const pileRow = (title, meta, pct, open = false) => `<div class="row">
    <div class="row-main">
      <span class="row-title">${title}</span>
      <span class="row-meta">${meta}</span>
    </div>
    <div class="mini-bar"><div class="mini-fill" style="width: ${pct}%;"></div></div>
    <span style="display: inline-flex; transform: rotate(${open ? 180 : 0}deg);">${chev}</span>
  </div>`

  const fileRow = (name, kind, meta, tier, tierLabel, extra = '') => `<div class="file-row">
    <div class="file-icon">${kind}</div>
    <div class="row-main">
      <span class="row-title" style="font-weight: 500;">${name}</span>
      <span class="row-meta">${meta}</span>
      ${extra}
      <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; align-items: center;">
        <span class="select">${tierLabel} ${chev}</span>
        <span class="button ghost small">Exclude</span>
        <span class="button ghost small">Delete</span>
      </div>
    </div>
  </div>`

  const SOURCES_BODY = `
<section class="card" style="display: flex; flex-direction: column; gap: 8px;">
  <h2>Sources</h2>
  <p class="muted small">Add files here to build learning points and Tutor questions. Saving a file or note is local. Open a pile to preview Build learning material, then choose Send this batch to authorize the model checks.</p>
  <p class="muted small"><strong>Source confidence</strong> — your judgment of the material’s accuracy and usefulness for learning. It is not mastery, priority, difficulty, or evidence that a claim has been verified.</p>
</section>

<section class="card" style="display: flex; flex-direction: column; gap: 8px;">
  <h2>New pile</h2>
  <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
    <div class="input" style="flex: 1 1 200px; min-height: 40px; padding: 8px 10px; color: var(--muted); font-size: 15px;">Antimicrobial stewardship</div>
    <span class="tier-option"><span class="dot" style="background: var(--tier-low);"></span>Low</span>
    <span class="tier-option on"><span class="dot" style="background: var(--tier-mid);"></span>Medium</span>
    <span class="tier-option"><span class="dot" style="background: var(--tier-high);"></span>High</span>
    <span class="button primary" style="min-height: 40px;">Create pile</span>
  </div>
</section>

<section class="card" style="display: flex; flex-direction: column; gap: 4px;">
  <h2 style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-high">High</span><span>2 piles</span></h2>
  ${pileRow('Influenza — IDSA guideline and lectures', '4 files · 2 notes · 18 points · 14 questions · 71% processed', 71, true)}
  <div style="padding: 8px 0 4px 8px; border-left: 2px solid var(--line); margin: 4px 0 8px 4px; display: flex; flex-direction: column; gap: 10px;">
    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
      <span class="button small">Add files</span>
      <span class="row-meta">PDF, PPTX, DOCX, TXT, MD · text is extracted; scans and image-only slides need a text alternative</span>
    </div>
    <div>
      <h4 style="margin-bottom: 4px;">Files in this pile</h4>
      ${fileRow('IDSA influenza guideline (lecture deck).pptx', 'PPTX', '42 slides · text in 40 of 42 units · 2 image-only · 148,300 chars', 'high', 'High')}
      ${fileRow('Uyeki 2019 IDSA seasonal influenza CPG.pdf', 'PDF', '47 pages · text in 47 of 47 units · 96,900 chars', 'high', 'High')}
      ${fileRow('Flu grand rounds notes.md', 'MD', '1 unit · 6,200 chars', 'high', 'High')}
      ${fileRow('Ward teaching — antivirals.pptx', 'PPTX', '18 slides · text in 3 of 18 units · 15 image-only', 'high', 'High', '<p class="warn small" style="padding: 6px 10px; margin-top: 4px;">Mostly image-only slides. No OCR or diagram interpretation is performed; only 3 slides will be read.</p>')}
    </div>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <h4>Build learning material</h4>
      <p class="small" style="color: var(--muted);">184,200 of 251,400 extracted-text characters processed; 67,200 remain. The next batch continues from where the last one stopped.</p>
      <div class="usage-bar"><div class="usage-fill" style="width: 73%;"></div></div>
      <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px;">
        <span class="button primary">Build learning material</span>
        <span class="row-meta" style="align-self: center;">Shows exactly which excerpts would be sent before anything leaves this Mac.</span>
      </div>
    </div>
    <div style="display: flex; flex-direction: column; gap: 4px;">
      <h4>Plain-text notes</h4>
      <div class="row" style="min-height: 36px;"><div class="row-main"><span class="row-title" style="font-weight: 500;">Baloxavir PEP household trial — secondary attack rates</span><span class="row-meta">Journal club · 2 notes</span></div><span class="button small">Use as source</span></div>
      <span class="button ghost small" style="align-self: flex-start;">Add a note</span>
    </div>
    <span class="button ghost small" style="align-self: flex-start; color: var(--fail);">Delete this pile</span>
  </div>
  ${pileRow('ATS/IDSA community-acquired pneumonia', '1 file · 0 notes · 9 points · 7 questions · fully processed', 100)}
</section>

<section class="card" style="display: flex; flex-direction: column; gap: 4px;">
  <h2 style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-mid">Medium</span><span>3 piles</span></h2>
  ${pileRow('Transplant ID handouts 2025', '3 files · 1 note · 6 points · 4 questions · 40% processed', 40)}
  ${pileRow('Board review — nephrology', '2 files · 0 notes · 0 points · 0 questions · not started', 0)}
  ${pileRow('Journal club, spring', '1 file · 4 notes · 2 points · 1 question · fully processed', 100)}
</section>

<section class="card" style="display: flex; flex-direction: column; gap: 4px;">
  <h2 style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-low">Low</span><span>1 pile</span></h2>
  ${pileRow('Conference slides (unverified)', '1 file · 0 notes · 3 points · 0 questions · fully processed', 100)}
</section>`

  // ---------- Improvement Map: a graph, not a list ----------
  // Nodes: topics. Size = open flags. Cluster colour = specialty. Edge = topics that share a
  // source pile or a learning point. "Not filed yet" sits apart, unlinked, in muted grey.
  const C = { id: '#2f5d50', idT: '#e6efec', pulm: '#b07d2b', pulmT: '#fdf3e3', renal: '#7b8794', renalT: '#eef0f2', none: '#9aa2ad', noneT: '#edeae2' }
  const NODES = [
    { id: 'influenza', label: 'Influenza', x: 300, y: 250, open: 4, done: 2, c: 'id', sel: true },
    { id: 'pneumonia', label: 'Pneumonia', x: 420, y: 340, open: 3, done: 1, c: 'id' },
    { id: 'stew', label: 'Antimicrobial stewardship', x: 560, y: 260, open: 2, done: 0, c: 'id' },
    { id: 'strongy', label: 'Strongyloides', x: 210, y: 420, open: 1, done: 0, c: 'id' },
    { id: 'endo', label: 'Endocarditis', x: 640, y: 380, open: 1, done: 2, c: 'id' },
    { id: 'txid', label: 'Transplant ID', x: 180, y: 320, open: 2, done: 1, c: 'id' },
    { id: 'ards', label: 'ARDS', x: 470, y: 470, open: 1, done: 0, c: 'pulm' },
    { id: 'vent', label: 'Mechanical ventilation', x: 340, y: 500, open: 1, done: 1, c: 'pulm' },
    { id: 'aki', label: 'AKI', x: 700, y: 170, open: 1, done: 0, c: 'renal' },
    { id: 'hypona', label: 'Hyponatremia', x: 760, y: 280, open: 0, done: 2, c: 'renal' },
    { id: 'unfiled', label: 'Not filed yet', x: 120, y: 150, open: 3, done: 0, c: 'none' }
  ]
  const EDGES = [['influenza', 'pneumonia'], ['pneumonia', 'stew'], ['influenza', 'vent'], ['pneumonia', 'ards'], ['txid', 'strongy'], ['endo', 'stew'], ['aki', 'hypona'], ['influenza', 'txid'], ['ards', 'vent']]
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]))
  const radius = (n) => 10 + 4 * n.open + 1.5 * n.done

  function graphSvg(w, h, rScale) {
    // Map the hand-placed layout onto the frame with padding, non-uniformly, so a phone frame uses its height.
    const xs = NODES.map((n) => n.x), ys = NODES.map((n) => n.y)
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
    const px = 44, py = 36
    const sx = (x) => px + ((x - minX) / (maxX - minX)) * (w - 2 * px)
    const sy = (y) => py + ((y - minY) / (maxY - minY)) * (h - py - 44)
    const edges = EDGES.map(([a, b]) => `<line x1="${sx(byId[a].x).toFixed(1)}" y1="${sy(byId[a].y).toFixed(1)}" x2="${sx(byId[b].x).toFixed(1)}" y2="${sy(byId[b].y).toFixed(1)}" stroke="#dcd7cc" stroke-width="1.5"></line>`).join('')
    const nodes = NODES.map((n) => {
      const r = radius(n) * rScale, cx = sx(n.x).toFixed(1), cy = sy(n.y).toFixed(1)
      const stroke = C[n.c], fill = C[n.c + 'T']
      const ring = n.sel ? `<circle cx="${cx}" cy="${cy}" r="${r + 5}" fill="none" stroke="${stroke}" stroke-width="1.5" stroke-dasharray="3 3"></circle>` : ''
      return `${ring}<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${n.open > 0 ? 2 : 1}"></circle>
      <text x="${cx}" y="${(Number(cy) + r + 13).toFixed(1)}" text-anchor="middle" font-family="-apple-system, 'Helvetica Neue', system-ui, sans-serif" font-size="${n.sel ? 12 : 11}" font-weight="${n.sel ? 600 : 400}" fill="${n.c === 'none' ? '#5c6470' : '#1c1e22'}">${n.label}</text>`
    }).join('')
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="Topic graph" style="display: block; background: var(--paper); border-radius: 8px; border: 1px solid var(--line); max-width: 100%;">
      ${edges}${nodes}
    </svg>`
  }

  const zoomBtn = (glyph) => `<span class="button" style="min-width: 36px; min-height: 36px; padding: 0; font-family: var(--sans);">${glyph}</span>`
  const MAP_CONTROLS = `<div style="display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
    <span class="badge" style="background: var(--accent-tint); border-color: var(--accent-line); color: var(--accent);">Open flags</span>
    <span class="badge">All</span>
    <span class="badge">By specialty</span>
    <span style="flex: 1 1 auto;"></span>
    ${zoomBtn('−')}${zoomBtn('+')}<span class="button" style="min-height: 36px; font-size: 13px; padding: 0 10px; font-family: var(--sans);">Fit</span>
  </div>`
  const MAP_LEGEND = `<div style="display: flex; flex-wrap: wrap; gap: 12px; font: 12px/1.4 var(--sans); color: var(--muted); align-items: center;">
    <span style="display: inline-flex; align-items: center; gap: 5px;"><span class="dot" style="background: ${C.idT}; border: 2px solid ${C.id};"></span>Infectious disease</span>
    <span style="display: inline-flex; align-items: center; gap: 5px;"><span class="dot" style="background: ${C.pulmT}; border: 2px solid ${C.pulm};"></span>Pulmonary / critical care</span>
    <span style="display: inline-flex; align-items: center; gap: 5px;"><span class="dot" style="background: ${C.renalT}; border: 2px solid ${C.renal};"></span>Nephrology</span>
    <span style="display: inline-flex; align-items: center; gap: 5px;"><span class="dot" style="background: ${C.noneT}; border: 1px solid ${C.none};"></span>Not filed</span>
    <span>· size = open flags · line = shared source or learning point</span>
  </div>`

  const flagLine = (text, when, addressed = false) => `<li style="display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid var(--line); padding-bottom: 8px;">
    <span class="title" style="font-size: 15px;">${text}</span>
    <span style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;"><span class="muted small">${when}</span>${addressed ? '<span class="badge" style="font-size: 12px;">Addressed</span>' : '<span class="button ghost small" style="min-height: 28px; font-size: 12px; padding: 0 8px;">Mark addressed</span>'}</span>
  </li>`
  const SELECTED_PANEL = `<section class="card" style="display: flex; flex-direction: column; gap: 8px; border-color: var(--accent-line);">
    <div style="display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;">
      <h3>Influenza</h3><span class="muted small">4 open · 2 addressed · linked to Pneumonia, Transplant ID, Mechanical ventilation</span>
    </div>
    <ul class="list" style="display: flex; flex-direction: column; gap: 8px;">
      ${flagLine('Unsure how long to continue antivirals in a ventilated flu patient', 'flagged 2 days ago')}
      ${flagLine('Baloxavir vs oseltamivir for PEP in immunocompromised contacts', 'flagged 5 days ago')}
      ${flagLine('When to add empiric MRSA coverage in post-influenza pneumonia', 'flagged 1 week ago')}
      ${flagLine('Timing of influenza vaccination after rituximab', 'flagged 2 weeks ago')}
      ${flagLine('Oseltamivir dosing in CRRT', 'flagged 3 weeks ago', true)}
    </ul>
    <p class="muted small">Tutor has 14 questions on this topic ready and 1 held. <a href="#">Open Sources</a> to see which pile they came from.</p>
  </section>`

  const MAP_INTRO = `<p class="muted small">Where the gaps are, as a map rather than a list. Drag a topic to move it, tap it to see its flags, pinch or scroll to zoom. Nothing here is a queue; it describes, it does not assign.</p>`

  const MAP_PHONE = `
<section class="card" style="display: flex; flex-direction: column; gap: 10px; padding: 12px;">
  <h2>Where the gaps are</h2>
  ${MAP_INTRO}
  ${MAP_CONTROLS}
  ${graphSvg(332, 420, 0.8)}
  ${MAP_LEGEND}
  <p class="muted small">3 flags have no topic yet. Filing them is the system’s job; they are safe where they are.</p>
</section>
${SELECTED_PANEL}
<section class="card" style="display: flex; flex-direction: column; gap: 8px;">
  <h2>Material behind it</h2>
  <ul class="tiers" style="display: flex; flex-direction: column; gap: 6px;">
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-high">High</span><span class="muted small">2 piles · 5 files</span></li>
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-mid">Medium</span><span class="muted small">3 piles · 6 files</span></li>
    <li style="display: flex; align-items: center; gap: 8px;"><span class="tier tier-low">Low</span><span class="muted small">1 pile · 1 file</span></li>
  </ul>
</section>`

  const MAP_DESKTOP = `
<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Where the gaps are</h2>
  ${MAP_INTRO}
  ${MAP_CONTROLS}
  <div style="display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start;">
    ${graphSvg(760, 560, 1)}
    <div style="display: flex; flex-direction: column; gap: 12px;">
      ${SELECTED_PANEL.replace('class="card"', 'class="card" style="padding: 12px;"')}
    </div>
  </div>
  ${MAP_LEGEND}
  <p class="muted small">3 flags have no topic yet. Filing them is the system’s job; they are safe where they are.</p>
</section>`

  // ---------- Model: the connection, and nothing else ----------
  const usage = (label, pct, resets) => `<li style="display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--line); padding-bottom: 8px;">
    <span style="display: flex; justify-content: space-between; gap: 8px;"><span class="title" style="font-size: 15px;">${label}</span><span class="muted small">${pct}% used · resets ${resets}</span></span>
    <div class="usage-bar"><div class="usage-fill" style="width: ${pct}%;"></div></div>
  </li>`
  const WHAT_IS_SENT = `<section class="card privacy" style="display: flex; flex-direction: column; gap: 4px;">
  <h2>What this page sends</h2>
  <dl style="margin: 0;">
    <dt>Checking sign-in and usage</dt>
    <dd>Goes through Codex on this Mac, which contacts OpenAI to answer it. No note, question or answer is included.</dd>
    <dt>Build and Grade</dt>
    <dd>Are the only two actions that send study content, and each shows what it will send first. Codex holds your ChatGPT sign-in, and <strong>no API key is used</strong> — model use draws on your ChatGPT plan and its usage limits.</dd>
  </dl>
</section>`

  const MODEL_SIGNED_IN = `
<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Model connection</h2>
  <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px;">
    <span class="badge" style="background: var(--accent); border-color: var(--accent); color: var(--accent-ink);">Signed in</span>
    <span class="muted small">Plus plan</span>
  </div>
  <p class="body">Codex on this Mac is signed in to ChatGPT. Build and Grade will work; nothing has been sent by opening this page.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <span class="button">Check again</span>
    <span class="button ghost">Restart the connection</span>
  </div>
</section>
<section class="card" style="display: flex; flex-direction: column; gap: 8px;">
  <h3>Plan usage</h3>
  <ul class="list" style="display: flex; flex-direction: column; gap: 8px;">
    ${usage('5-hour window', 38, 'in 2 h 10 min')}
    ${usage('Weekly', 61, 'Monday 09:00')}
  </ul>
  <p class="muted small">Reported by Codex for your ChatGPT plan. Vademecum does not see an account name or address.</p>
</section>
${WHAT_IS_SENT}`

  const MODEL_SIGN_IN = `
<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h2>Model connection</h2>
  <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px;">
    <span class="badge">Signed out</span>
  </div>
  <p class="body">Codex on this Mac is not signed in to ChatGPT. Reading your notes and questions still works; Build and Grade will not until you sign in.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <span class="button">Check again</span>
    <span class="button ghost">Restart the connection</span>
  </div>
</section>
<section class="card" style="display: flex; flex-direction: column; gap: 10px;">
  <h3>Sign in with ChatGPT</h3>
  <ol class="steps" style="margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 10px;">
    <li>Open <a href="#">the verification page</a> in any browser — this app will not open it for you.</li>
    <li>Enter this one-time code: <span class="device-code">ABCD-EFGH</span></li>
    <li>Come back here and press <strong>Check again</strong>.</li>
  </ol>
  <p class="muted small">The code is shown once and stored nowhere. Closing this page loses it; cancel and start again if that happens.</p>
  <div class="actions" style="display: flex; flex-wrap: wrap; gap: 8px;">
    <span class="button primary">Check again</span>
    <span class="button ghost">Cancel sign-in</span>
  </div>
</section>
${WHAT_IS_SENT}`

  return {
    'Sources.dc.html': page({
      extraCss: DENSE,
      body: phoneWrap(`${headerPhone('Sources')}<main class="stack" style="display: flex; flex-direction: column; gap: 12px;">${SOURCES_BODY}</main>${FOOTER}`)
    }),
    'SourcesDesktop.dc.html': page({
      extraCss: DENSE,
      body: desktopWrap(`${headerDesktop('Sources')}${column(`<main class="stack" style="display: flex; flex-direction: column; gap: 12px;">${SOURCES_BODY}</main>${FOOTER}`)}`)
    }),
    'ImprovementMap.dc.html': page({
      extraCss: DENSE,
      body: phoneWrap(`${headerPhone('Improvement Map')}<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">${MAP_PHONE}</main>${FOOTER}`)
    }),
    'ImprovementMapDesktop.dc.html': page({
      extraCss: DENSE,
      body: desktopWrap(`${headerDesktop('Improvement Map')}<div class="stack" style="display: flex; flex-direction: column; gap: 16px; max-width: 1136px; width: 100%; margin: 0 auto;"><main class="stack" style="display: flex; flex-direction: column; gap: 16px;">${MAP_DESKTOP}</main>${FOOTER}</div>`)
    }),
    'Model.dc.html': page({
      body: phoneWrap(`${headerPhone('Model')}<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">${MODEL_SIGNED_IN}</main>${FOOTER}`)
    }),
    'ModelSignIn.dc.html': page({
      body: phoneWrap(`${headerPhone('Model')}<main class="stack" style="display: flex; flex-direction: column; gap: 16px;">${MODEL_SIGN_IN}</main>${FOOTER}`)
    })
  }
}
