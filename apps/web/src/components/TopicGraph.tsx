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
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { CoveredTopic, MapPosition, ReportArea, Specialty, TopicGap, TopicLink } from '../lib/types'

export interface GraphNode extends SimulationNodeDatum {
  id: string
  label: string
  open: number
  addressed: number
  points: number
  specialty: string | null
  unfiled: boolean
  /** What the newest exam report said about this area (ADR 0020), if it named it. */
  standing: 'below' | 'at' | 'above' | null
}

interface GraphLink extends SimulationLinkDatum<GraphNode> {
  weight: number
}

export const UNFILED_ID = '__not_filed__'
/** The legend key for topics with no specialty; also what the filter hides them by. */
export const UNASSIGNED = '__unassigned__'
const EMPTY_SET: ReadonlySet<string> = new Set()
// A stable default: a fresh array each render would rebuild the layout every render.
const NO_REPORTS: ReportArea[] = []

/** Whether a node is drawn under the current legend filter. Unfiled flags are never filtered. */
export function isShown(node: Pick<GraphNode, 'specialty' | 'unfiled'>, hidden: ReadonlySet<string>): boolean {
  if (node.unfiled) return true
  return !hidden.has(node.specialty ?? UNASSIGNED)
}

/** 44px hit target even when the drawn circle is smaller (ADR 0004). */
const HIT_RADIUS = 22
const LAYOUT_TICKS = 300

export function radiusFor(node: Pick<GraphNode, 'open' | 'addressed' | 'points'>): number {
  return 8 + 3.5 * node.open + Math.min(6, 1.5 * node.addressed) + Math.min(6, 0.75 * node.points)
}

/** Build the node and link lists the layout will use. Exported for tests. */
export function buildGraph(
  topics: TopicGap[],
  covered: CoveredTopic[],
  links: TopicLink[],
  { openOnly, reports = [] }: { openOnly: boolean; reports?: ReportArea[] }
): { nodes: GraphNode[]; links: GraphLink[] } {
  const nodes = new Map<string, GraphNode>()
  for (const gap of topics) {
    if (openOnly && gap.open_flags === 0) continue
    const id = gap.topic ?? UNFILED_ID
    nodes.set(id, {
      id,
      label: gap.topic ?? 'Not filed yet',
      open: gap.open_flags,
      addressed: gap.addressed_flags,
      points: 0,
      specialty: gap.specialty?.id ?? null,
      unfiled: gap.topic === null,
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
        unfiled: false,
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
        unfiled: false,
        standing: area.standing
      })
    }
  }
  const edges: GraphLink[] = []
  for (const link of links) {
    const a = nodes.get(link.a)
    const b = nodes.get(link.b)
    if (a && b) edges.push({ source: a, target: b, weight: link.weight })
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
export function layout(
  nodes: GraphNode[],
  links: GraphLink[],
  width: number,
  height: number,
  remembered: MapPosition[] = []
): void {
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
    // A gentle pull to the middle keeps unlinked topics on the page instead of
    // drifting off it; forceCenter alone would let them fly.
    .force('x', forceX<GraphNode>(width / 2).strength(0.06))
    .force('y', forceY<GraphNode>(height / 2).strength(0.09))
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
        forceX<GraphNode>((node) => anchors.get(node.id)?.x ?? width / 2).strength((node) =>
          anchors.has(node.id) ? 0.8 : 0.06
        )
      )
      .force(
        'y',
        forceY<GraphNode>((node) => anchors.get(node.id)?.y ?? height / 2).strength((node) =>
          anchors.has(node.id) ? 0.8 : 0.09
        )
      )
      .alpha(0.12)
    for (let i = 0; i < SETTLE_TICKS; i += 1) simulation.tick()
  }
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
export function specialtyClass(specialty: string | null, unfiled: boolean): string {
  if (unfiled) return 'node-unfiled'
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
  onToggleSpecialty
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
}) {
  const width = 720
  const height = 480
  const graph = useMemo(() => {
    const built = buildGraph(topics, covered, links, { openOnly, reports })
    layout(built.nodes, built.links, width, height, positions)
    return built
    // `positions` is deliberately not a dependency: it is the memory the layout
    // starts from, and re-running on every save would make the graph twitch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topics, covered, links, openOnly, reports])

  // Report the settled layout once per build, so new topics get remembered too.
  const report = useRef(onPositions)
  report.current = onPositions
  useEffect(() => {
    report.current?.(positionsOf(graph.nodes))
  }, [graph])

  const present = useMemo(() => {
    const ids = new Set(graph.nodes.map((node) => node.specialty).filter((id): id is string => id !== null))
    return specialties.filter((entry) => ids.has(entry.id))
  }, [graph, specialties])
  const hasUnassigned = graph.nodes.some((node) => !node.unfiled && node.specialty === null)

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
      onSelect(selected === current.node.id ? null : current.node.id)
    }
    if (current?.kind === 'node' && current.moved && openOnly) {
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
          {graph.links.map((link) => {
            const source = link.source as GraphNode
            const target = link.target as GraphNode
            if (!isShown(source, hidden) || !isShown(target, hidden)) return null
            return (
              <line
                key={`${source.id}|${target.id}`}
                className="topic-edge"
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
            return (
              <g
                key={node.id}
                className={`topic-node ${specialtyClass(node.specialty, node.unfiled)} ${node.standing ? `standing-${node.standing}` : ''} ${isSelected ? 'selected' : ''}`}
                transform={`translate(${node.x ?? 0} ${node.y ?? 0})`}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={`${node.label}: ${node.open} open, ${node.addressed} addressed, ${node.points} points${node.standing ? `, exam standing ${node.standing}` : ''}`}
                onPointerDown={(event) => onNodePointerDown(event, node)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect(isSelected ? null : node.id)
                  }
                }}
              >
                <circle className="topic-hit" r={Math.max(HIT_RADIUS, r)} />
                {isSelected ? <circle className="topic-ring" r={r + 5} /> : null}
                {node.standing === 'below' ? <circle className="standing-ring" r={r + 4} /> : null}
                <circle className="topic-dot" r={r} strokeWidth={node.open > 0 ? 2 : 1} />
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
