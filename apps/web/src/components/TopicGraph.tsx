/**
 * The Improvement Map as a graph.
 *
 * Nodes are topics. A node's size is how many open flags it carries; its
 * colour is the specialty it belongs to — the owner's assignment, or a match
 * on the topic's own name, or grey when neither. An edge means one generated
 * learning point was filed under both topics. Flags alone never draw an edge.
 *
 * The layout is computed synchronously on every open. Topics that have a
 * remembered position are anchored there while the new ones settle around
 * them, then everything is released for a few gentle ticks so a crowded
 * corner can breathe. The result — and every drag — is reported back through
 * `onPositions` so the next open starts from the same picture.
 *
 * Nothing here is a queue. It describes; it does not assign.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent } from 'react'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import type { Simulation, SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { CoveredTopic, MapPage, MapPosition, ReportArea, Specialty, TopicGap, TopicLink } from '../lib/types'

export interface GraphNode extends SimulationNodeDatum {
  id: string
  label: string
  open: number
  addressed: number
  points: number
  specialty: string | null
  /** What the newest exam report said about this area (ADR 0020), if it named it. */
  standing: 'below' | 'at' | 'above' | null
  /** A topic, or an encyclopedia page a flagged topic is about (feedback of 6 October). */
  kind?: 'topic' | 'page'
  entryId?: string
}

export interface GraphLink extends SimulationLinkDatum<GraphNode> {
  weight: number
  /** shared: one learning point under both topics; covers: a topic and its page; pages: two pages sharing points. */
  kind?: 'shared' | 'covers' | 'pages'
}

export const PAGE_PREFIX = 'page:'

/** The legend key for topics with no specialty; also what the filter hides them by. */
export const UNASSIGNED = '__unassigned__'
const EMPTY_SET: ReadonlySet<string> = new Set()
// A stable default: a fresh array each render would rebuild the layout every render.
const NO_REPORTS: ReportArea[] = []
const NO_PAGES: MapPage[] = []
const NO_PAGE_LINKS: { topic: string; entry_id: string }[] = []
const NO_LINKS: TopicLink[] = []

/** Whether a node is drawn under the current legend filter. */
export function isShown(node: Pick<GraphNode, 'specialty'>, hidden: ReadonlySet<string>): boolean {
  return !hidden.has(node.specialty ?? UNASSIGNED)
}

/** 44px hit target even when the drawn circle is smaller (ADR 0004). */
const HIT_RADIUS = 22
const LAYOUT_TICKS = 300

export function radiusFor(node: Pick<GraphNode, 'open' | 'addressed' | 'points'> & { kind?: GraphNode['kind'] }): number {
  if (node.kind === 'page') return 7
  return 8 + 3.5 * node.open + Math.min(6, 1.5 * node.addressed) + Math.min(6, 0.75 * node.points)
}

/** Build the node and link lists the layout will use. Exported for tests. */
export function buildGraph(
  topics: TopicGap[],
  covered: CoveredTopic[],
  links: TopicLink[],
  {
    openOnly,
    reports = [],
    pages = [],
    pageLinks = [],
    pageEdges = []
  }: {
    openOnly: boolean
    reports?: ReportArea[]
    pages?: MapPage[]
    pageLinks?: { topic: string; entry_id: string }[]
    pageEdges?: TopicLink[]
  }
): { nodes: GraphNode[]; links: GraphLink[] } {
  const nodes = new Map<string, GraphNode>()
  for (const gap of topics) {
    // A flag with no topic yet is not a place on the map; it stays in the list underneath.
    if (gap.topic === null) continue
    if (openOnly && gap.open_flags === 0) continue
    nodes.set(gap.topic, {
      id: gap.topic,
      label: gap.topic,
      open: gap.open_flags,
      addressed: gap.addressed_flags,
      points: 0,
      specialty: gap.specialty?.id ?? null,
      standing: null
    })
  }
  for (const entry of covered) {
    const existing = nodes.get(entry.topic)
    if (existing) {
      existing.points = entry.point_count
      if (existing.specialty === null) existing.specialty = entry.specialty?.id ?? null
    } else if (!openOnly) {
      nodes.set(entry.topic, {
        id: entry.topic,
        label: entry.topic,
        open: 0,
        addressed: 0,
        points: entry.point_count,
        specialty: entry.specialty?.id ?? null,
        standing: null
      })
    }
  }
  // An exam report's areas are nodes too: the newest report's word on each
  // area wins, and an area below the mark is drawn whether or not anything
  // was flagged under it.
  for (const area of reports) {
    const existing = nodes.get(area.topic)
    if (existing) {
      if (existing.standing === null) existing.standing = area.standing
      if (existing.specialty === null) existing.specialty = area.specialty_id
    } else if (!openOnly || area.standing === 'below') {
      nodes.set(area.topic, {
        id: area.topic,
        label: area.topic,
        open: 0,
        addressed: 0,
        points: 0,
        specialty: area.specialty_id,
        standing: area.standing
      })
    }
  }
  const edges: GraphLink[] = []
  for (const link of links) {
    const a = nodes.get(link.a)
    const b = nodes.get(link.b)
    if (a && b) edges.push({ source: a, target: b, weight: link.weight, kind: 'shared' })
  }
  // Each topic's encyclopedia pages, as small nodes of their own: two flags about the
  // same page meet there, and pages that share learning points are joined.
  const byId = new Map(pages.map((page) => [page.id, page]))
  for (const link of pageLinks) {
    const topic = nodes.get(link.topic)
    const page = byId.get(link.entry_id)
    if (!topic || !page) continue
    const id = PAGE_PREFIX + page.id
    let node = nodes.get(id)
    if (!node) {
      node = { id, label: page.title, open: 0, addressed: 0, points: 0, specialty: page.specialty_id ?? topic.specialty, standing: null, kind: 'page', entryId: page.id }
      nodes.set(id, node)
    }
    edges.push({ source: topic, target: node, weight: 2, kind: 'covers' })
  }
  for (const edge of pageEdges) {
    const a = nodes.get(PAGE_PREFIX + edge.a)
    const b = nodes.get(PAGE_PREFIX + edge.b)
    if (a && b) edges.push({ source: a, target: b, weight: edge.weight, kind: 'pages' })
  }
  return { nodes: [...nodes.values()], links: edges }
}

const SETTLE_TICKS = 40

/**
 * Run the force layout to rest. Remembered nodes are pinned where they were
 * while the rest settle, then everything is released for a few low-energy
 * ticks. With no memory the start is a deterministic ring, so the same data
 * gives the same picture. Exported for tests.
 */
/**
 * Where each specialty's area sits: evenly round the middle, in a stable order, so
 * topics of one specialty gather (feedback of 6 October). Exported for tests.
 */
export function specialtyCentres(nodes: GraphNode[], width: number, height: number): Map<string, { x: number; y: number }> {
  const ids = [...new Set(nodes.map((node) => node.specialty).filter((id): id is string => id !== null))].sort()
  const centres = new Map<string, { x: number; y: number }>()
  const radius = Math.min(width, height) * 0.3
  ids.forEach((id, index) => {
    const angle = (index / Math.max(1, ids.length)) * Math.PI * 2 - Math.PI / 2
    centres.set(id, ids.length === 1 ? { x: width / 2, y: height / 2 } : { x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius })
  })
  return centres
}

export function layout(
  nodes: GraphNode[],
  links: GraphLink[],
  width: number,
  height: number,
  remembered: MapPosition[] = []
): Simulation<GraphNode, GraphLink> {
  const centres = specialtyCentres(nodes, width, height)
  const homeX = (node: GraphNode) => (node.specialty !== null ? centres.get(node.specialty)?.x : undefined) ?? width / 2
  const homeY = (node: GraphNode) => (node.specialty !== null ? centres.get(node.specialty)?.y : undefined) ?? height / 2
  const anchors = new Map(remembered.map((entry) => [entry.topic, entry]))
  const count = Math.max(1, nodes.length)
  const ring = Math.min(width, height) * 0.32
  nodes.forEach((node, index) => {
    const anchor = anchors.get(node.id)
    if (anchor) {
      node.x = anchor.x
      node.y = anchor.y
      node.fx = anchor.x
      node.fy = anchor.y
    } else {
      const angle = (index / count) * Math.PI * 2
      node.x = width / 2 + Math.cos(angle) * ring
      node.y = height / 2 + Math.sin(angle) * ring
      node.fx = null
      node.fy = null
    }
  })
  const simulation = forceSimulation<GraphNode>(nodes)
    .force('charge', forceManyBody<GraphNode>().strength(-220))
    .force(
      'link',
      forceLink<GraphNode, GraphLink>(links)
        .distance((link) => 110 - Math.min(40, link.weight * 8))
        .strength((link) => Math.min(1, 0.3 + link.weight * 0.15))
    )
    .force('collide', forceCollide<GraphNode>((node) => radiusFor(node) + 18))
    // A gentle pull toward the topic's specialty area: topics of one specialty gather,
    // and unlinked ones stay on the page instead of drifting off it.
    .force('x', forceX<GraphNode>(homeX).strength(0.08))
    .force('y', forceY<GraphNode>(homeY).strength(0.1))
    .stop()
  for (let i = 0; i < LAYOUT_TICKS; i += 1) simulation.tick()
  if (anchors.size > 0) {
    // Release the pins, but keep each remembered node on a short leash to its
    // old spot: crowding can nudge it, a re-layout cannot move it.
    for (const node of nodes) {
      node.fx = null
      node.fy = null
    }
    simulation
      // Links and charge have done their work on the newcomers; in this phase
      // they only resolve overlaps, so they are turned well down.
      .force('charge', forceManyBody<GraphNode>().strength(-60))
      .force('collide', forceCollide<GraphNode>((node) => radiusFor(node) + 18).strength(0.4))
      .force(
        'link',
        forceLink<GraphNode, GraphLink>(links)
          .distance((link) => 110 - Math.min(40, link.weight * 8))
          .strength(0.1)
      )
      .force(
        'x',
        forceX<GraphNode>((node) => anchors.get(node.id)?.x ?? homeX(node)).strength((node) =>
          anchors.has(node.id) ? 0.8 : 0.06
        )
      )
      .force(
        'y',
        forceY<GraphNode>((node) => anchors.get(node.id)?.y ?? homeY(node)).strength((node) =>
          anchors.has(node.id) ? 0.8 : 0.09
        )
      )
      .alpha(0.12)
    for (let i = 0; i < SETTLE_TICKS; i += 1) simulation.tick()
  }
  // Settled and stopped; a drag wakes it, so neighbours follow (feedback of 6 October).
  simulation
    .force('x', forceX<GraphNode>(homeX).strength(0.05))
    .force('y', forceY<GraphNode>(homeY).strength(0.06))
    .force('charge', forceManyBody<GraphNode>().strength(-160))
    .force('collide', forceCollide<GraphNode>((node) => radiusFor(node) + 14))
    .force(
      'link',
      forceLink<GraphNode, GraphLink>(links)
        .distance((link) => (link.kind === 'covers' ? 60 : 110 - Math.min(40, link.weight * 8)))
        .strength((link) => (link.kind === 'covers' ? 0.6 : Math.min(1, 0.3 + link.weight * 0.15)))
    )
    .alpha(0)
    .stop()
  return simulation
}

/** The layout as the server stores it: every node, graph units, two decimals. */
export function positionsOf(nodes: GraphNode[]): MapPosition[] {
  return nodes.map((node) => ({
    topic: node.id,
    x: Math.round((node.x ?? 0) * 100) / 100,
    y: Math.round((node.y ?? 0) * 100) / 100
  }))
}

/** A transform that shows every node, labels included, without exceeding 1.5x. */
export function fitView(nodes: GraphNode[], width: number, height: number): { x: number; y: number; k: number } {
  if (nodes.length === 0) return { x: 0, y: 0, k: 1 }
  const pad = 36
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const node of nodes) {
    const r = radiusFor(node)
    minX = Math.min(minX, (node.x ?? 0) - Math.max(r, 40))
    maxX = Math.max(maxX, (node.x ?? 0) + Math.max(r, 40))
    minY = Math.min(minY, (node.y ?? 0) - r)
    maxY = Math.max(maxY, (node.y ?? 0) + r + 16)
  }
  const k = Math.min(1.5, (width - 2 * pad) / Math.max(1, maxX - minX), (height - 2 * pad) / Math.max(1, maxY - minY))
  const x = (width - (minX + maxX) * k) / 2
  const y = (height - (minY + maxY) * k) / 2
  return { x, y, k }
}

function pinchDistance(points: Map<number, { x: number; y: number }>): number {
  const [p, q] = [...points.values()]
  if (!p || !q) return 1
  return Math.hypot(p.x - q.x, p.y - q.y)
}

/** The CSS hook for a node's colour. Specialty ids are slugs, so they are safe in a class name. */
export function specialtyClass(specialty: string | null): string {
  return specialty === null ? 'node-unassigned' : `spec-${specialty}`
}

export function TopicGraph({
  topics,
  covered,
  links,
  specialties,
  positions,
  openOnly,
  reports = NO_REPORTS,
  selected,
  onSelect,
  onPositions,
  hidden = EMPTY_SET,
  onToggleSpecialty,
  pages = NO_PAGES,
  pageLinks = NO_PAGE_LINKS,
  pageEdges = NO_LINKS,
  onOpenPage
}: {
  topics: TopicGap[]
  covered: CoveredTopic[]
  links: TopicLink[]
  specialties: Specialty[]
  positions: MapPosition[]
  openOnly: boolean
  selected: string | null
  onSelect: (id: string | null) => void
  /** Called with the whole layout after it settles and after every drag. */
  onPositions?: (positions: MapPosition[]) => void
  /** What exam reports said, by area (ADR 0020). */
  reports?: ReportArea[]
  /** Specialty ids (or UNASSIGNED) the owner has switched off in the legend. */
  hidden?: ReadonlySet<string>
  onToggleSpecialty?: (id: string) => void
  /** Each flagged topic's encyclopedia pages, drawn as small nodes (feedback of 6 October). */
  pages?: MapPage[]
  pageLinks?: { topic: string; entry_id: string }[]
  pageEdges?: TopicLink[]
  onOpenPage?: (entryId: string) => void
}) {
  const width = 720
  const height = 480
  // Re-arrange: lay out afresh by specialty, forgetting the remembered positions once.
  const [fresh, setFresh] = useState(0)
  const graph = useMemo(() => {
    const built = buildGraph(topics, covered, links, { openOnly, reports, pages, pageLinks, pageEdges })
    const simulation = layout(built.nodes, built.links, width, height, fresh > 0 ? [] : positions)
    return { ...built, simulation }
    // `positions` is deliberately not a dependency: it is the memory the layout
    // starts from, and re-running on every save would make the graph twitch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topics, covered, links, openOnly, reports, pages, pageLinks, pageEdges, fresh])

  // Live after it settles: a drag wakes the simulation and neighbours follow; when it
  // comes to rest again the layout is remembered.
  useEffect(() => {
    const simulation = graph.simulation
    simulation.on('tick', () => rerender((value) => value + 1))
    simulation.on('end', () => report.current?.(positionsOf(graph.nodes)))
    return () => {
      simulation.on('tick', null).on('end', null).stop()
    }
  }, [graph])

  // A selected node's neighbours stay bright; everything else dims.
  const neighbours = useMemo(() => {
    if (selected === null) return null
    const ids = new Set<string>([selected])
    for (const link of graph.links) {
      const a = (link.source as GraphNode).id
      const b = (link.target as GraphNode).id
      if (a === selected) ids.add(b)
      if (b === selected) ids.add(a)
    }
    return ids
  }, [graph, selected])

  // Report the settled layout once per build, so new topics get remembered too.
  const report = useRef(onPositions)
  report.current = onPositions
  useEffect(() => {
    report.current?.(positionsOf(graph.nodes))
  }, [graph])

  // Each specialty's area, drawn softly behind its topics, with its name.
  const regions = (() => {
    const groups = new Map<string, GraphNode[]>()
    for (const node of graph.nodes) {
      if (node.specialty === null || !isShown(node, hidden)) continue
      groups.set(node.specialty, [...(groups.get(node.specialty) ?? []), node])
    }
    return [...groups.entries()]
      .filter(([, members]) => members.length >= 2)
      .map(([id, members]) => {
        const cx = members.reduce((sum, node) => sum + (node.x ?? 0), 0) / members.length
        const cy = members.reduce((sum, node) => sum + (node.y ?? 0), 0) / members.length
        const r = Math.max(...members.map((node) => Math.hypot((node.x ?? 0) - cx, (node.y ?? 0) - cy) + radiusFor(node))) + 18
        return { id, cx, cy, r, name: specialties.find((entry) => entry.id === id)?.name ?? '' }
      })
    // Recomputed on every render, so it follows the nodes as they move: cheap.
  })()

  const present = useMemo(() => {
    const ids = new Set(graph.nodes.map((node) => node.specialty).filter((id): id is string => id !== null))
    return specialties.filter((entry) => ids.has(entry.id))
  }, [graph, specialties])
  const hasUnassigned = graph.nodes.some((node) => node.specialty === null)

  // View transform: pan and zoom, in SVG user units. The initial view fits the
  // laid-out nodes into the frame with room for their labels.
  const fitted = useMemo(() => fitView(graph.nodes, width, height), [graph])
  const [view, setView] = useState(fitted)
  useEffect(() => setView(fitted), [fitted])
  const [, rerender] = useState(0)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const drag = useRef<
    | { kind: 'node'; node: GraphNode; moved: boolean; pointerId: number }
    | { kind: 'pan'; startX: number; startY: number; originX: number; originY: number; pointerId: number }
    | null
  >(null)
  const pinch = useRef<Map<number, { x: number; y: number }>>(new Map())
  const pinchStart = useRef<{ distance: number; k: number } | null>(null)

  const toGraph = (clientX: number, clientY: number) => {
    const svg = svgRef.current
    if (!svg) return { x: 0, y: 0 }
    const rect = svg.getBoundingClientRect()
    const sx = (clientX - rect.left) * (width / Math.max(1, rect.width))
    const sy = (clientY - rect.top) * (height / Math.max(1, rect.height))
    return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k }
  }

  const zoomBy = (factor: number) =>
    setView((current) => {
      const k = Math.max(0.4, Math.min(3, current.k * factor))
      // Zoom about the centre of the frame.
      const cx = width / 2
      const cy = height / 2
      return { k, x: cx - ((cx - current.x) * k) / current.k, y: cy - ((cy - current.y) * k) / current.k }
    })

  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    event.preventDefault()
    zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1)
  }

  const onNodePointerDown = (event: ReactPointerEvent<SVGGElement>, node: GraphNode) => {
    event.stopPropagation()
    ;(event.currentTarget as Element).setPointerCapture?.(event.pointerId)
    drag.current = { kind: 'node', node, moved: false, pointerId: event.pointerId }
  }

  const tapNode = (node: GraphNode) => {
    if (node.kind === 'page' && node.entryId && onOpenPage) {
      onOpenPage(node.entryId)
      return
    }
    onSelect(selected === node.id ? null : node.id)
  }

  const onBackgroundPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    pinch.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (pinch.current.size === 2) {
      pinchStart.current = { distance: pinchDistance(pinch.current), k: view.k }
      drag.current = null
      return
    }
    drag.current = {
      kind: 'pan',
      startX: event.clientX,
      startY: event.clientY,
      originX: view.x,
      originY: view.y,
      pointerId: event.pointerId
    }
  }

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (pinch.current.has(event.pointerId)) {
      pinch.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    }
    if (pinch.current.size === 2 && pinchStart.current) {
      const distance = pinchDistance(pinch.current)
      const k = Math.max(0.4, Math.min(3, (pinchStart.current.k * distance) / Math.max(1, pinchStart.current.distance)))
      setView((current) => ({ ...current, k }))
      return
    }
    const current = drag.current
    if (!current) return
    if (current.kind === 'node') {
      const point = toGraph(event.clientX, event.clientY)
      current.node.x = point.x
      current.node.y = point.y
      current.node.fx = point.x
      current.node.fy = point.y
      if (!current.moved) graph.simulation.alphaTarget(0.25).restart()
      current.moved = true
      rerender((value) => value + 1)
    } else {
      const svg = svgRef.current
      const scale = svg ? width / Math.max(1, svg.getBoundingClientRect().width) : 1
      setView((v) => ({
        ...v,
        x: current.originX + (event.clientX - current.startX) * scale,
        y: current.originY + (event.clientY - current.startY) * scale
      }))
    }
  }

  const onPointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
    pinch.current.delete(event.pointerId)
    if (pinch.current.size < 2) pinchStart.current = null
    const current = drag.current
    drag.current = null
    if (current?.kind === 'node' && !current.moved) {
      tapNode(current.node)
    }
    if (current?.kind === 'node' && current.moved) {
      // It stays where it was put; the rest settle round it and the layout is remembered.
      graph.simulation.alphaTarget(0)
      onPositions?.(positionsOf(graph.nodes))
    }
  }

  return (
    <div className="topic-graph">
      <div className="topic-graph-controls">
        <button type="button" className="button small" onClick={() => zoomBy(1 / 1.25)} aria-label="Zoom out">
          −
        </button>
        <button type="button" className="button small" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
          +
        </button>
        <button type="button" className="button small" onClick={() => setView(fitted)}>
          Fit
        </button>
        <button type="button" className="button small" onClick={() => setFresh((value) => value + 1)}>
          Re-arrange
        </button>
      </div>
      <svg
        ref={svgRef}
        className="topic-graph-canvas"
        viewBox={`0 0 ${width} ${height}`}
        role="group"
        aria-label="Topic graph"
        onWheel={onWheel}
        onPointerDown={onBackgroundPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {regions.map((region) => (
            <g key={region.id} className={`spec-region spec-${region.id}`} aria-hidden="true">
              <circle cx={region.cx} cy={region.cy} r={region.r} />
              <text x={region.cx} y={region.cy - region.r + 12} textAnchor="middle">
                {region.name}
              </text>
            </g>
          ))}
          {graph.links.map((link) => {
            const source = link.source as GraphNode
            const target = link.target as GraphNode
            if (!isShown(source, hidden) || !isShown(target, hidden)) return null
            const lit = neighbours !== null && (source.id === selected || target.id === selected)
            const dim = neighbours !== null && !lit
            return (
              <line
                key={`${source.id}|${target.id}`}
                className={`topic-edge edge-${link.kind ?? 'shared'}${lit ? ' lit' : ''}${dim ? ' dim' : ''}`}
                x1={source.x ?? 0}
                y1={source.y ?? 0}
                x2={target.x ?? 0}
                y2={target.y ?? 0}
                strokeWidth={1 + Math.min(3, link.weight * 0.5)}
              />
            )
          })}
          {graph.nodes.map((node) => {
            if (!isShown(node, hidden)) return null
            const r = radiusFor(node)
            const isSelected = selected === node.id
            const dim = neighbours !== null && !neighbours.has(node.id)
            return (
              <g
                key={node.id}
                className={`topic-node ${node.kind === 'page' ? 'page-node' : ''} ${specialtyClass(node.specialty)} ${node.standing ? `standing-${node.standing}` : ''} ${isSelected ? 'selected' : ''} ${dim ? 'dim' : ''}`}
                transform={`translate(${node.x ?? 0} ${node.y ?? 0})`}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={
                  node.kind === 'page'
                    ? `Encyclopedia page ${node.label}: open it`
                    : `${node.label}: ${node.open} open, ${node.addressed} addressed, ${node.points} points${node.standing ? `, exam standing ${node.standing}` : ''}`
                }
                onPointerDown={(event) => onNodePointerDown(event, node)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    tapNode(node)
                  }
                }}
              >
                <circle className="topic-hit" r={Math.max(HIT_RADIUS, r)} />
                {isSelected ? <circle className="topic-ring" r={r + 5} /> : null}
                {node.standing === 'below' ? <circle className="standing-ring" r={r + 4} /> : null}
                {node.kind === 'page' ? (
                  <rect className="topic-dot page-dot" x={-r} y={-r} width={r * 2} height={r * 2} rx={2} />
                ) : (
                  <circle className="topic-dot" r={r} strokeWidth={node.open > 0 ? 2 : 1} />
                )}
                <text className="topic-label" y={r + 13} textAnchor="middle">
                  {node.label}
                </text>
              </g>
            )
          })}
        </g>
      </svg>
      <ul className="graph-legend" aria-label="Specialties shown">
        {present.map((entry) => (
          <li key={entry.id} className={`spec-${entry.id}`}>
            <LegendToggle id={entry.id} label={entry.name} hidden={hidden.has(entry.id)} onToggle={onToggleSpecialty} />
          </li>
        ))}
        {hasUnassigned ? (
          <li className="node-unassigned">
            <LegendToggle id={UNASSIGNED} label="No specialty yet" hidden={hidden.has(UNASSIGNED)} onToggle={onToggleSpecialty} />
          </li>
        ) : null}
      </ul>
    </div>
  )
}

/**
 * A legend entry that doubles as a filter. Pressed means shown; the swatch is
 * the colour, the label is the name, and hiding never deletes anything.
 */
function LegendToggle({
  id,
  label,
  hidden,
  onToggle
}: {
  id: string
  label: string
  hidden: boolean
  onToggle?: (id: string) => void
}) {
  return (
    <button
      type="button"
      className={`legend-toggle ${hidden ? 'off' : ''}`}
      aria-pressed={!hidden}
      onClick={() => onToggle?.(id)}
      disabled={onToggle === undefined}
    >
      <span className="legend-swatch" aria-hidden="true" />
      {label}
    </button>
  )
}
