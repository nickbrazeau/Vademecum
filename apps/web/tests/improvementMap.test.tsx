/**
 * The Improvement Map as a graph: the facts it draws, and what it refuses to
 * invent. A link is one learning point filed under two topics; a flag alone
 * links nothing; the layout is the same for the same data.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UNASSIGNED, UNFILED_ID, buildGraph, isShown, layout, positionsOf, radiusFor, specialtyClass } from '../src/components/TopicGraph'
import type { GraphNode } from '../src/components/TopicGraph'
import { ImprovementMap } from '../src/pages/ImprovementMap'
import type { CoveredTopic, Specialty, TopicGap, TopicLink } from '../src/lib/types'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

const ID = { id: 'infectious-disease', name: 'Infectious Disease', assigned_by: 'owner' as const }
const RENAL = { id: 'nephrology', name: 'Nephrology', assigned_by: 'name' as const }
const specialties: Specialty[] = [
  { id: 'general-internal-medicine', name: 'General Internal Medicine' },
  { id: 'nephrology', name: 'Nephrology' },
  { id: 'infectious-disease', name: 'Infectious Disease' }
]
const flu: TopicGap = { topic: 'Influenza', open_flags: 3, addressed_flags: 1, last_flagged_at: null, cluster: { id: 'p1', title: 'Flu pile', tier: 'high' }, specialty: ID }
const cap: TopicGap = { topic: 'Pneumonia', open_flags: 1, addressed_flags: 0, last_flagged_at: null, cluster: { id: 'p1', title: 'Flu pile', tier: 'high' }, specialty: null }
const renal: TopicGap = { topic: 'Nephrology', open_flags: 0, addressed_flags: 2, last_flagged_at: null, cluster: null, specialty: RENAL }
const unfiled: TopicGap = { topic: null, open_flags: 2, addressed_flags: 0, last_flagged_at: null, cluster: null, specialty: null }
const covered: CoveredTopic[] = [
  { topic: 'Influenza', point_count: 4, cluster: { id: 'p1', title: 'Flu pile', tier: 'high' }, specialty: ID },
  { topic: 'Antivirals', point_count: 2, cluster: { id: 'p1', title: 'Flu pile', tier: 'high' }, specialty: null }
]
const links: TopicLink[] = [
  { a: 'Influenza', b: 'Pneumonia', weight: 2 },
  { a: 'Antivirals', b: 'Influenza', weight: 1 },
  { a: 'Influenza', b: 'Nephrology', weight: 1 }
]

describe('building the graph', () => {
  it('draws only flagged topics by default, and covered topics on request', () => {
    const open = buildGraph([flu, cap, renal, unfiled], covered, links, { openOnly: true })
    expect(open.nodes.map((node) => node.id).sort()).toEqual([UNFILED_ID, 'Influenza', 'Pneumonia'].sort())
    // Nephrology has no open flags and Antivirals is only covered: neither is drawn,
    // so neither can carry an edge.
    expect(open.links).toHaveLength(1)

    const all = buildGraph([flu, cap, renal, unfiled], covered, links, { openOnly: false })
    expect(all.nodes.map((node) => node.id).sort()).toEqual(
      [UNFILED_ID, 'Antivirals', 'Influenza', 'Nephrology', 'Pneumonia'].sort()
    )
    expect(all.links).toHaveLength(3)
  })

  it('never links an unfiled flag to anything', () => {
    const graph = buildGraph([flu, unfiled], covered, [{ a: 'Influenza', b: UNFILED_ID, weight: 1 }], { openOnly: true })
    // The server never emits the sentinel; if it ever did, it still would not be a topic.
    expect(graph.links).toHaveLength(1)
    expect(graph.nodes.find((node) => node.id === UNFILED_ID)?.unfiled).toBe(true)
  })

  it('sizes a node by open flags first', () => {
    expect(radiusFor({ open: 3, addressed: 0, points: 0 })).toBeGreaterThan(radiusFor({ open: 0, addressed: 3, points: 3 }))
  })

  it('lays the same data out the same way every time', () => {
    const first = buildGraph([flu, cap, renal], covered, links, { openOnly: false })
    const second = buildGraph([flu, cap, renal], covered, links, { openOnly: false })
    layout(first.nodes, first.links, 720, 480)
    layout(second.nodes, second.links, 720, 480)
    const positions = (nodes: GraphNode[]) => nodes.map((node) => [node.id, Math.round(node.x ?? 0), Math.round(node.y ?? 0)])
    expect(positions(first.nodes)).toEqual(positions(second.nodes))
    for (const node of first.nodes) {
      expect(node.x).toBeGreaterThan(0)
      expect(node.x).toBeLessThan(720)
      expect(node.y).toBeGreaterThan(0)
      expect(node.y).toBeLessThan(480)
    }
  })

  it('keeps a remembered topic close to where it was, and settles new ones around it', () => {
    const graph = buildGraph([flu, cap, renal], covered, links, { openOnly: false })
    const remembered = [
      { topic: 'Influenza', x: 120, y: 90 },
      { topic: 'Nephrology', x: 600, y: 400 }
    ]
    layout(graph.nodes, graph.links, 720, 480, remembered)
    const at = (id: string) => graph.nodes.find((node) => node.id === id)!
    // Anchored while the others settle, then released on a short leash: a drift
    // of a few percent of the frame is allowed, a re-layout is not.
    expect(Math.hypot(at('Influenza').x! - 120, at('Influenza').y! - 90)).toBeLessThan(40)
    expect(Math.hypot(at('Nephrology').x! - 600, at('Nephrology').y! - 400)).toBeLessThan(40)
    // The new node is not on the deterministic ring; it settled near what it links to.
    expect(Math.hypot(at('Pneumonia').x! - at('Influenza').x!, at('Pneumonia').y! - at('Influenza').y!)).toBeLessThan(200)
    // Nothing is left pinned, so dragging still works afterwards.
    expect(graph.nodes.every((node) => node.fx === null && node.fy === null)).toBe(true)
    // What is reported back is every node, rounded, in graph units.
    const reported = positionsOf(graph.nodes)
    expect(reported.map((entry) => entry.topic).sort()).toEqual(['Antivirals', 'Influenza', 'Nephrology', 'Pneumonia'])
    expect(reported.every((entry) => Number.isFinite(entry.x) && Number.isFinite(entry.y))).toBe(true)
  })

  it('colours by specialty, never by pile', () => {
    expect(specialtyClass('infectious-disease', false)).toBe('spec-infectious-disease')
    expect(specialtyClass(null, false)).toBe('node-unassigned')
    expect(specialtyClass('nephrology', true)).toBe('node-unfiled')
    const graph = buildGraph([flu, cap], covered, [], { openOnly: true })
    expect(graph.nodes.find((node) => node.id === 'Influenza')?.specialty).toBe('infectious-disease')
    expect(graph.nodes.find((node) => node.id === 'Pneumonia')?.specialty).toBeNull()
  })
})

describe('the Improvement Map page', () => {
  const map = {
    topics: [flu, cap, renal, unfiled],
    covered_topics: covered,
    links,
    specialties,
    positions: [{ topic: 'Influenza', x: 200, y: 150 }],
    confidences: [
      { confidence: 'low', label: 'Low', meaning: '', pile_count: 0, source_count: 0 },
      { confidence: 'mid', label: 'Medium', meaning: '', pile_count: 0, source_count: 0 },
      { confidence: 'high', label: 'High', meaning: '', pile_count: 1, source_count: 2 }
    ],
    unfiled_flag_count: 2,
    bank: {}
  }
  const flags = [
    { id: 'f1', text: 'Antiviral duration on the ventilator', topic: 'Influenza', status: 'open', created_at: 'now', updated_at: 'now' },
    { id: 'f2', text: 'Baloxavir for PEP', topic: 'Influenza', status: 'open', created_at: 'now', updated_at: 'now' },
    { id: 'f3', text: 'MRSA coverage in severe CAP', topic: 'Pneumonia', status: 'open', created_at: 'now', updated_at: 'now' },
    { id: 'f4', text: 'Something unfiled', topic: null, status: 'open', created_at: 'now', updated_at: 'now' }
  ]

  it('draws a node per flagged topic, keeps the full list, and selects a topic to show its flags', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => json(String(url).includes('/flags') ? flags : map)))
    render(<ImprovementMap reloadToken={0} />)
    const graph = await screen.findByRole('group', { name: 'Topic graph' })
    const nodes = within(graph).getAllByRole('button')
    expect(nodes.map((node) => node.getAttribute('aria-label'))).toEqual(
      expect.arrayContaining([expect.stringMatching(/^Influenza: 3 open/), expect.stringMatching(/^Pneumonia: 1 open/), expect.stringMatching(/^Not filed yet: 2 open/)])
    )
    expect(nodes.some((node) => node.getAttribute('aria-label')?.startsWith('Nephrology'))).toBe(false)

    // The list underneath is still the whole record.
    const everything = screen.getByRole('region', { name: /everything you have flagged/i })
    expect(within(everything).getAllByRole('listitem')).toHaveLength(4)

    const user = userEvent.setup()
    await user.click(within(graph).getByRole('button', { name: /^Influenza/ }))
    const panel = await screen.findByRole('region', { name: 'Influenza' })
    expect(panel).toHaveTextContent('3 open · 1 addressed · 4 learning points · mostly from Flu pile')
    expect(panel).toHaveTextContent(/linked to Pneumonia/)
    expect(within(panel).getAllByRole('listitem')).toHaveLength(2)
    expect(within(panel).queryByText('MRSA coverage in severe CAP')).not.toBeInTheDocument()

    // The legend names only the specialties actually drawn, plus the unassigned marker.
    const legend = screen.getByRole('list', { name: 'Specialties shown' })
    expect(legend).toHaveTextContent('Infectious Disease')
    expect(legend).toHaveTextContent('No specialty yet')
    expect(legend).not.toHaveTextContent('Nephrology')

    await user.click(screen.getByRole('button', { name: 'Everything covered' }))
    const all = within(screen.getByRole('group', { name: 'Topic graph' })).getAllByRole('button')
    expect(all.some((node) => node.getAttribute('aria-label')?.startsWith('Antivirals'))).toBe(true)
    expect(all.some((node) => node.getAttribute('aria-label')?.startsWith('Nephrology'))).toBe(true)
  })

  it('remembers the settled layout once, and records the owner\'s specialty call', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const calls: { path: string; method: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = String(url)
      calls.push({ path, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : null })
      if (path.includes('/topics/specialty')) return json({ topic: 'Pneumonia', specialty: { ...specialties[0], assigned_by: 'owner' } })
      if (path.includes('/positions')) return json({ saved: 3 })
      return json(path.includes('/flags') ? flags : map)
    }))
    render(<ImprovementMap reloadToken={0} />)
    await screen.findByRole('group', { name: 'Topic graph' })
    // One save, a moment after the layout settles — never one per tick.
    await vi.advanceTimersByTimeAsync(700)
    const saves = calls.filter((call) => call.path.endsWith('/improvement-map/positions'))
    expect(saves).toHaveLength(1)
    expect(saves[0]!.method).toBe('PUT')
    const sent = (saves[0]!.body as { positions: { topic: string; x: number; y: number }[] }).positions
    expect(sent.map((entry) => entry.topic).sort()).toEqual([UNFILED_ID, 'Influenza', 'Pneumonia'].sort())
    // The remembered Influenza position was honoured, within a small drift.
    const influenza = sent.find((entry) => entry.topic === 'Influenza')!
    expect(Math.hypot(influenza.x - 200, influenza.y - 150)).toBeLessThan(40)

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(within(screen.getByRole('group', { name: 'Topic graph' })).getByRole('button', { name: /^Pneumonia/ }))
    const panel = await screen.findByRole('region', { name: 'Pneumonia' })
    await user.selectOptions(within(panel).getByRole('combobox', { name: /specialty/i }), 'general-internal-medicine')
    const assigned = calls.find((call) => call.path.endsWith('/improvement-map/topics/specialty'))
    expect(assigned?.method).toBe('PUT')
    expect(assigned?.body).toEqual({ topic: 'Pneumonia', specialty_id: 'general-internal-medicine' })
    vi.useRealTimers()
  })

  it('lets the legend switch a specialty off, and never hides an unfiled flag', async () => {
    expect(isShown({ specialty: 'nephrology', unfiled: false }, new Set(['nephrology']))).toBe(false)
    expect(isShown({ specialty: null, unfiled: false }, new Set([UNASSIGNED]))).toBe(false)
    expect(isShown({ specialty: null, unfiled: true }, new Set([UNASSIGNED]))).toBe(true)

    vi.stubGlobal('fetch', vi.fn(async (url: string) => json(String(url).includes('/flags') ? flags : map)))
    render(<ImprovementMap reloadToken={0} />)
    const graph = await screen.findByRole('group', { name: 'Topic graph' })
    const user = userEvent.setup()
    const idToggle = screen.getByRole('button', { name: 'Infectious Disease' })
    expect(idToggle).toHaveAttribute('aria-pressed', 'true')

    await user.click(idToggle)
    expect(idToggle).toHaveAttribute('aria-pressed', 'false')
    const drawn = within(graph).getAllByRole('button').map((node) => node.getAttribute('aria-label') ?? '')
    expect(drawn.some((label) => label.startsWith('Influenza'))).toBe(false)
    expect(drawn.some((label) => label.startsWith('Pneumonia'))).toBe(true)
    expect(drawn.some((label) => label.startsWith('Not filed yet'))).toBe(true)

    // A selected topic that gets filtered out loses its panel rather than lingering.
    await user.click(idToggle)
    await user.click(within(graph).getByRole('button', { name: /^Influenza/ }))
    expect(await screen.findByRole('region', { name: 'Influenza' })).toBeVisible()
    await user.click(idToggle)
    expect(screen.queryByRole('region', { name: 'Influenza' })).not.toBeInTheDocument()

    // The legend is still a legend: nothing about the data changed.
    expect(within(screen.getByRole('region', { name: /everything you have flagged/i })).getAllByRole('listitem')).toHaveLength(4)
  })

  it('says so when nothing has been flagged, and draws nothing', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => json(String(url).includes('/flags') ? [] : { ...map, topics: [], covered_topics: [], links: [], unfiled_flag_count: 0 })))
    render(<ImprovementMap reloadToken={0} />)
    const region = await screen.findByRole('region', { name: /where the gaps are/i })
    expect(region).toHaveTextContent(/nothing flagged yet/i)
    expect(screen.queryByRole('group', { name: 'Topic graph' })).not.toBeInTheDocument()
  })
})
