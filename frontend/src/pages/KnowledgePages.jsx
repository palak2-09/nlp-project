import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, ChevronDown, ChevronRight, Download, FileText, GitMerge, LoaderCircle, Network, RefreshCw, Search as SearchIcon, Sparkles, Undo2, X } from 'lucide-react'
import {
  askQuestion, exportGraph, getAnalytics, getEvaluation, getDocuments, getEntities, getEntity, getEntityCandidates, getEntityMerges,
  getGraph, getNeighbors, getSettings, mergeEntities, searchKnowledge, shortestPath, syncGraph, undoEntityMerge,
} from '../api/client.js'

function Header({ eyebrow, title, subtitle, action }) {
  return <div className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>
}

function Loading() { return <div className="loading-state"><LoaderCircle size={18} className="spin" /> Loading workspace data…</div> }
function ErrorBox({ children }) { return <div className="knowledge-error">{children}</div> }
function Empty({ title, detail }) { return <div className="knowledge-empty"><Network size={23} /><strong>{title}</strong><span>{detail}</span></div> }

function createGraphForest(nodes, edges, preferredRootId) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const degreeById = new Map(nodes.map((node) => [node.id, 0]))
  const adjacency = new Map(nodes.map((node) => [node.id, []]))
  for (const edge of edges) {
    if (!nodeById.has(edge.subject.id) || !nodeById.has(edge.object.id)) continue
    degreeById.set(edge.subject.id, (degreeById.get(edge.subject.id) || 0) + 1)
    degreeById.set(edge.object.id, (degreeById.get(edge.object.id) || 0) + 1)
    adjacency.get(edge.subject.id).push({ neighborId: edge.object.id, edge })
    if (edge.subject.id !== edge.object.id) adjacency.get(edge.object.id).push({ neighborId: edge.subject.id, edge })
  }

  for (const links of adjacency.values()) {
    links.sort((left, right) => {
      const labelOrder = (nodeById.get(left.neighborId)?.label || '').localeCompare(nodeById.get(right.neighborId)?.label || '')
      return labelOrder || left.edge.predicate.localeCompare(right.edge.predicate)
    })
  }

  const candidateRoots = [...nodes].sort((left, right) => {
    const degreeOrder = (degreeById.get(right.id) || 0) - (degreeById.get(left.id) || 0)
    const mentionOrder = (right.mention_count || 0) - (left.mention_count || 0)
    return degreeOrder || mentionOrder || left.label.localeCompare(right.label)
  }).map((node) => node.id)
  const rootOrder = [...new Set([preferredRootId, ...candidateRoots].filter((id) => nodeById.has(id)))]
  const visited = new Set()
  const treeEdgeIds = new Set()

  const build = (id, parentId = null, link = null) => {
    visited.add(id)
    const children = []
    for (const next of adjacency.get(id) || []) {
      if (visited.has(next.neighborId)) continue
      treeEdgeIds.add(next.edge.id)
      children.push(build(next.neighborId, id, next.edge))
    }
    return { node: nodeById.get(id), parentId, link, children }
  }

  const roots = []
  for (const id of rootOrder) {
    if (!visited.has(id)) roots.push(build(id))
  }
  return { roots, degreeById, crossLinks: edges.filter((edge) => !treeEdgeIds.has(edge.id)) }
}

function GraphTreeBranch({ item, isRoot, collapsedIds, onToggle, onSelectNode, onSelectFact, selectedNodeId, degreeById }) {
  const expanded = !collapsedIds.has(item.node.id)
  const hasChildren = item.children.length > 0
  const branchDirection = item.link?.subject.id === item.parentId ? '→' : '←'
  return <li role="treeitem" aria-expanded={hasChildren ? expanded : undefined} className={`graph-tree-branch ${isRoot ? 'is-root' : ''}`}>
    {item.link && <button type="button" className="tree-branch-label" onClick={() => onSelectFact(item.link)} title="Open relationship evidence">
      <span>{branchDirection}</span><strong>{item.link.predicate.replaceAll('_', ' ')}</strong><small>{item.link.review_status}</small>
    </button>}
    <div className="tree-node-wrap">
      <button type="button" className={`tree-node-card ${selectedNodeId === item.node.id ? 'selected' : ''}`} onClick={() => onSelectNode(item.node)} aria-pressed={selectedNodeId === item.node.id}>
        <span className="tree-node-type">{item.node.entity_type}</span>
        <strong title={item.node.label}>{item.node.label}</strong>
        <span className="tree-node-meta">{item.node.mention_count || 0} mentions · {degreeById.get(item.node.id) || 0} relations</span>
      </button>
      {hasChildren && <button type="button" className="tree-toggle" onClick={() => onToggle(item.node.id)} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${item.node.label}`} title={`${expanded ? 'Collapse' : 'Expand'} branch`}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>}
    </div>
    {expanded && hasChildren && <ul role="group" className="graph-tree-children">{item.children.map((child) => <GraphTreeBranch key={child.node.id} item={child} collapsedIds={collapsedIds} onToggle={onToggle} onSelectNode={onSelectNode} onSelectFact={onSelectFact} selectedNodeId={selectedNodeId} degreeById={degreeById} />)}</ul>}
  </li>
}

export function GraphPage({ onError, onNotice }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [documents, setDocuments] = useState([])
  const [entityTypes, setEntityTypes] = useState([])
  const [entityType, setEntityType] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [reviewStatus, setReviewStatus] = useState('')
  const [query, setQuery] = useState('')
  const [selectedFact, setSelectedFact] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [selectedEntityDetail, setSelectedEntityDetail] = useState(null)
  const [entityLoading, setEntityLoading] = useState(false)
  const [rootId, setRootId] = useState('')
  const [collapsedIds, setCollapsedIds] = useState(new Set())
  const [pathStart, setPathStart] = useState('')
  const [pathEnd, setPathEnd] = useState('')
  const [pathResult, setPathResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showCrossLinks, setShowCrossLinks] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const [result, docResult, entityResult] = await Promise.all([
        getGraph({ entity_type: entityType || undefined, document_id: documentId || undefined, q: query.trim() || undefined, review_status: reviewStatus || undefined, limit: 250 }),
        getDocuments({ limit: 100 }),
        getEntities({ limit: 200 }),
      ])
      setGraph(result)
      setDocuments(docResult.items)
      setEntityTypes([...new Set(entityResult.items.map((item) => item.entity_type))].sort())
      setSelectedFact(null)
      setSelectedNode(null)
      setSelectedEntityDetail(null)
      setPathResult(null)
      setCollapsedIds(new Set())
    } catch (error) { onError(error.response?.data?.detail || 'Could not load graph data.') }
    finally { setLoading(false) }
  }
  useEffect(() => { const timer = window.setTimeout(load, query ? 250 : 0); return () => window.clearTimeout(timer) }, [entityType, documentId, reviewStatus, query])

  const nodes = graph.nodes || []
  const edges = graph.edges || []
  const suggestedRootId = useMemo(() => {
    const { roots } = createGraphForest(nodes, edges, '')
    return roots[0]?.node.id || ''
  }, [nodes, edges])
  const activeRootId = nodes.some((node) => node.id === rootId) ? rootId : suggestedRootId
  const forest = useMemo(() => createGraphForest(nodes, edges, activeRootId), [nodes, edges, activeRootId])
  const orderedNodes = useMemo(() => [...nodes].sort((left, right) => left.label.localeCompare(right.label)), [nodes])
  const relatedFacts = selectedNode ? edges.filter((edge) => edge.subject.id === selectedNode.id || edge.object.id === selectedNode.id) : []

  const selectNode = async (node) => {
    setSelectedNode(node)
    setSelectedFact(null)
    setSelectedEntityDetail(null)
    setEntityLoading(true)
    try { setSelectedEntityDetail(await getEntity(node.id)) }
    catch (error) { onError(error.response?.data?.detail || 'Could not load entity details.') }
    finally { setEntityLoading(false) }
  }
  const selectFact = (fact) => {
    setSelectedFact(fact)
    setSelectedNode(null)
    setSelectedEntityDetail(null)
  }
  const toggleBranch = (id) => setCollapsedIds((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const download = async (format) => {
    try {
      const blob = await exportGraph(format, documentId || undefined)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `papergraph-export.${format}`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch { onError('Graph export failed.') }
  }

  const findPath = async () => {
    if (!pathStart || !pathEnd) return
    try { setPathResult(await shortestPath({ source_entity_id: pathStart, target_entity_id: pathEnd, max_depth: 8 })) }
    catch (error) { setPathResult(null); onError(error.response?.data?.detail || 'Could not find a path between those entities.') }
  }

  return <div className="page-content knowledge-page">
    <Header eyebrow="CONNECTED COLLECTION" title="Graph explorer" subtitle="Start from an entity, expand its branches, and inspect each fact beside its source evidence." action={<div className="header-actions"><button className="button secondary" onClick={() => download('json')}><Download size={14} /> JSON</button><button className="button secondary" onClick={() => download('csv')}><Download size={14} /> CSV</button></div>} />
    <div className="graph-filters panel"><div className="search-box"><SearchIcon size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find entities in graph" /></div>
      <select className="filter-select" value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="">All entity types</option>{entityTypes.map((type) => <option key={type}>{type}</option>)}</select>
      <select className="filter-select" value={documentId} onChange={(event) => setDocumentId(event.target.value)}><option value="">All documents</option>{documents.map((item) => <option key={item.id} value={item.id}>{item.filename}</option>)}</select>
      <select className="filter-select" value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)}><option value="">All review states</option><option value="approved">Approved</option><option value="pending">Needs review</option><option value="rejected">Rejected</option></select>
      <button className="icon-button" onClick={load} title="Refresh"><RefreshCw size={15} /></button>
    </div>
    {loading ? <Loading /> : !nodes.length ? <div className="panel"><Empty title="No graph data yet" detail="Upload or paste text, then wait for its extraction job to finish." /></div> : <div className="graph-layout">
      <section className="panel graph-canvas-panel">
        <div className="graph-canvas-toolbar"><span><Network size={15} /> {nodes.length} entities · {edges.length} relations{graph.truncated ? ' · more available; use search or filters' : ''}</span><button className="icon-button" onClick={load} title="Refresh tree"><RefreshCw size={14} /></button></div>
        <div className="tree-controls"><label><span>Start tree at</span><select className="filter-select" aria-label="Tree root entity" value={activeRootId} onChange={(event) => { setRootId(event.target.value); setCollapsedIds(new Set()) }}>{orderedNodes.map((node) => <option key={node.id} value={node.id}>{node.label} · {node.entity_type}</option>)}</select></label><span className="tree-help">Select a card for source mentions. Select a branch label for its fact evidence.</span></div>
        <div className="graph-tree-scroll">
          <div className="graph-tree-forest">{forest.roots.map((root, index) => <section className="tree-component" key={root.node.id}>
            {index > 0 && <div className="tree-component-title">Separate connected group</div>}
            <ul className="graph-tree" role="tree" aria-label={index === 0 ? 'Knowledge graph tree' : 'Separate knowledge graph tree'}>
              <GraphTreeBranch item={root} isRoot collapsedIds={collapsedIds} onToggle={toggleBranch} onSelectNode={selectNode} onSelectFact={selectFact} selectedNodeId={selectedNode?.id} degreeById={forest.degreeById} />
            </ul>
          </section>)}</div>
        </div>
        {forest.crossLinks.length > 0 && <section className="tree-crosslinks"><button type="button" className="tree-crosslinks-heading" aria-expanded={showCrossLinks} onClick={() => setShowCrossLinks((value) => !value)}><span>Additional connections</span><span>{forest.crossLinks.length} · {showCrossLinks ? 'hide' : 'show'}</span></button>{showCrossLinks && <div className="tree-crosslinks-list">{forest.crossLinks.map((fact) => <button type="button" className="tree-crosslink" key={fact.id} onClick={() => selectFact(fact)}><strong>{fact.subject.label}</strong><span>{fact.predicate.replaceAll('_', ' ')}</span><strong>{fact.object.label}</strong><small>{fact.review_status}</small></button>)}</div>}</section>}
        <div className="graph-legend"><span>→ follows fact direction</span><span>Branch labels open evidence</span><span>Use “Start tree at” to inspect another root</span></div>
      </section>
      <aside className="panel graph-detail-panel">
        <div className="graph-detail-head"><strong>{selectedFact ? 'Fact details' : selectedNode ? 'Entity details' : 'Path finder'}</strong><button className="icon-button" onClick={() => { setSelectedFact(null); setSelectedNode(null); setSelectedEntityDetail(null) }} title="Clear selection"><X size={15} /></button></div>
        {selectedFact ? <div className="graph-detail-content"><div className="fact-assertion"><strong>{selectedFact.subject.label}</strong><span className="predicate-chip">{selectedFact.predicate}</span><strong>{selectedFact.object.label}</strong></div><div className="tree-detail-meta"><span>{selectedFact.review_status}</span><span>{selectedFact.polarity}</span><span>{Math.round((selectedFact.confidence || 0) * 100)}% confidence</span><span>{selectedFact.extraction_method}</span></div><h4>Source evidence</h4>{selectedFact.evidence?.length ? selectedFact.evidence.map((evidence, index) => <article className="evidence-block" key={`${evidence.document_id}-${index}`}><div className="evidence-source"><FileText size={13} /><strong>{evidence.filename}</strong><span>{evidence.location_label}</span></div><p>“{evidence.evidence_text}”</p></article>) : <p>No source evidence was returned for this relation.</p>}</div>
          : selectedNode ? <div className="graph-detail-content">
            {entityLoading ? <Loading /> : selectedEntityDetail && <>
              <span className="entity-type">{selectedEntityDetail.entity_type}</span><h3>{selectedEntityDetail.label}</h3>
              <p><strong>Normalized name:</strong> {selectedEntityDetail.normalized_name}</p>
              <p><strong>Source mentions:</strong> {selectedEntityDetail.mention_count}</p>
              {selectedEntityDetail.aliases?.length > 0 && <p><strong>Aliases:</strong> {selectedEntityDetail.aliases.join(', ')}</p>}
              <div className="tree-detail-actions"><button className="button secondary full-button" onClick={async () => { try { const expanded = await getNeighbors(selectedNode.id, { document_id: documentId || undefined, review_status: reviewStatus || undefined, depth: 1 }); setGraph(expanded); setRootId(selectedNode.id); setCollapsedIds(new Set()) } catch (error) { onError(error.response?.data?.detail || 'Could not expand this entity.') } }}>Show this entity’s neighborhood <ArrowRight size={14} /></button><button className="button subtle full-button" onClick={() => { if (!pathStart) setPathStart(selectedNode.id); else setPathEnd(selectedNode.id) }}>Use in path finder</button></div>
              <section className="tree-detail-section"><h4>Relationships in this view <span>{relatedFacts.length}</span></h4>{relatedFacts.length ? relatedFacts.map((fact) => <button type="button" className="tree-related-fact" key={fact.id} onClick={() => selectFact(fact)}><strong>{fact.subject.id === selectedNode.id ? fact.object.label : fact.subject.label}</strong><span>{fact.predicate.replaceAll('_', ' ')} · {fact.review_status}</span><small>{fact.evidence?.length || 0} source excerpts</small></button>) : <p>No visible relations for this entity.</p>}</section>
              <section className="tree-detail-section"><h4>Source mentions <span>{selectedEntityDetail.mention_count} total</span></h4>{selectedEntityDetail.mention_count > (selectedEntityDetail.mentions?.length || 0) && <p>Showing the most recent {selectedEntityDetail.mentions?.length || 0} mentions.</p>}<div className="entity-mention-list">{selectedEntityDetail.mentions?.length ? selectedEntityDetail.mentions.map((mention, index) => <article className="entity-mention-card" key={`${mention.document_id}-${mention.start_offset}-${index}`}><div className="evidence-source"><FileText size={13} /><strong>{mention.filename}</strong><span>{mention.location_label}</span></div><p>“{mention.surface_form}”</p><small>{mention.extraction_method} · {Math.round((mention.confidence || 0) * 100)}% confidence · offsets {mention.start_offset}–{mention.end_offset}</small></article>) : <p>No source mentions are available.</p>}</div></section>
            </>}
          </div>
            : <div className="graph-detail-content path-form"><p>Select a node or relationship to inspect its details and source evidence.</p><label>From<select className="filter-select" value={pathStart} onChange={(event) => setPathStart(event.target.value)}><option value="">Choose entity</option>{orderedNodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label><label>To<select className="filter-select" value={pathEnd} onChange={(event) => setPathEnd(event.target.value)}><option value="">Choose entity</option>{orderedNodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label><button className="button primary full-button" disabled={!pathStart || !pathEnd} onClick={findPath}>Find shortest path <ArrowRight size={14} /></button>{pathResult && <div className="path-result">{pathResult.nodes.map((node, index) => <span key={node.id}>{index > 0 && <b>→</b>}{node.label}</span>)}</div>}</div>}
      </aside>
    </div>}
  </div>
}

export function SearchPage({ onError }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [mode, setMode] = useState('lexical')
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const submit = async (event) => {
    event.preventDefault()
    if (!query.trim()) return
    setLoading(true); setSearched(true)
    try { const response = await searchKnowledge(query.trim()); setResults(response.results); setMode(response.retrieval_mode || 'lexical'); if (response.embedding_note) onError(response.embedding_note) }
    catch (error) { onError(error.response?.data?.detail || 'Search failed.') }
    finally { setLoading(false) }
  }
  return <div className="page-content knowledge-page"><Header eyebrow="COLLECTION RETRIEVAL" title="Search" subtitle="Find source passages, entities, and extracted relationships across processed documents." />
    <form className="panel search-page-form" onSubmit={submit}><div className="search-box large-search"><SearchIcon size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search your documents and graph" autoFocus /><button className="button primary" type="submit" disabled={loading}>Search</button></div><span>Retrieval mode: {mode === 'lexical+semantic' ? 'BM25 + semantic similarity' : 'BM25 lexical ranking'}</span></form>
    {loading ? <Loading /> : !results.length && searched ? <div className="panel"><Empty title="No matches found" detail="Try different terms or process more documents." /></div> : results.length > 0 && <section className="panel search-results"><div className="panel-heading"><div><div className="eyebrow">MATCHES</div><h3>{results.length} results</h3></div></div>{results.map((result, index) => <article className="search-result" key={`${result.kind}-${result.id || result.document_id}-${index}`}><div className="search-result-kind">{result.kind}</div><div className="search-result-main"><strong>{result.title || result.filename}</strong><span>{result.subtitle || result.location_label}{result.filename && result.kind !== 'passage' ? ` · ${result.filename}` : ''}</span>{result.kind === 'passage' ? <p>“{result.text}”</p> : result.kind === 'fact' && <p>{result.fact.evidence?.[0]?.evidence_text || 'No evidence available'}</p>}</div><span className="search-score">{Math.round(result.score * 100)}%</span></article>)}</section>}
  </div>
}

export function AssistantPage({ onError }) {
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState([])
  const [loading, setLoading] = useState(false)
  const ask = async (event) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || loading) return
    setLoading(true); setQuestion('')
    try { const result = await askQuestion(value); setTurns((items) => [{ ...result, id: crypto.randomUUID() }, ...items]) }
    catch (error) { onError(error.response?.data?.detail || 'The assistant could not retrieve evidence.') }
    finally { setLoading(false) }
  }
  return <div className="page-content knowledge-page"><Header eyebrow="CITED ANSWERS" title="Knowledge assistant" subtitle="Ask about your collection. Answers use approved facts or show relevant source passages with citations." />
    <section className="panel assistant-intro"><Sparkles size={17} /><div><strong>Evidence first</strong><p>Without approved facts, the assistant says evidence is insufficient and shows matching passages. Enable the optional structured LLM provider in .env to generate an answer from those citations.</p></div></section>
    <form className="panel assistant-form" onSubmit={ask}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about your documents…" rows={3} /><div><span>Only your stored source material is searched.</span><button className="button primary" disabled={loading || !question.trim()}>{loading ? 'Thinking…' : 'Ask question'} <ArrowRight size={14} /></button></div></form>
    {loading && <Loading />}{turns.map((turn) => <article className="panel assistant-answer" key={turn.id}><div className="assistant-question">{turn.question}</div><div className="assistant-answer-text">{turn.answer}</div>{turn.insufficient_evidence && <div className="insufficient-tag">Insufficient evidence for a direct answer</div>}{turn.model_fallback && <div className="graph-review-state">Optional model was unavailable; showing deterministic evidence retrieval.</div>}<div className="citation-list">{turn.citations.map((citation, index) => <div className="citation-item" key={`${citation.document_id}-${citation.start_offset}-${index}`}><div><FileText size={13} /><strong>{citation.filename}</strong><span>{citation.location_label}</span></div><blockquote>“{citation.quote}”</blockquote></div>)}</div></article>)}
  </div>
}

export function AnalyticsPage({ onError }) {
  const [data, setData] = useState(null)
  const [evaluation, setEvaluation] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { Promise.all([getAnalytics(), getEvaluation()]).then(([stats, scores]) => { setData(stats); setEvaluation(scores) }).catch((error) => onError(error.response?.data?.detail || 'Could not load analytics.')).finally(() => setLoading(false)) }, [onError])
  if (loading) return <div className="page-content"><Loading /></div>
  if (!data) return <div className="page-content"><ErrorBox>Analytics could not be loaded.</ErrorBox></div>
  const chart = (items) => { const max = Math.max(1, ...items.map((item) => item.count)); return items.map((item) => <div className="bar-row" key={item.label}><span>{item.label.replaceAll('_', ' ')}</span><div><i style={{ width: `${Math.max(2, item.count / max * 100)}%` }} /></div><strong>{item.count}</strong></div>) }
  const totalReviews = Object.values(data.review_counts).reduce((sum, count) => sum + count, 0)
  return <div className="page-content knowledge-page"><Header eyebrow="COLLECTION HEALTH" title="Analytics" subtitle="Descriptive counts are computed from your real collection. Extraction quality metrics are never estimated from demo data." />
    <div className="analytics-summary"><div className="panel analytics-metric"><span>Average processing time</span><strong>{data.average_processing_seconds == null ? '—' : `${data.average_processing_seconds.toFixed(2)} s`}</strong></div><div className="panel analytics-metric"><span>Entity names with aliases</span><strong>{data.entities_with_aliases}</strong></div><div className="panel analytics-metric"><span>Human review decisions</span><strong>{totalReviews}</strong></div><div className="panel analytics-metric"><span>Approved rate</span><strong>{data.review_acceptance_rate == null ? '—' : `${(data.review_acceptance_rate * 100).toFixed(1)}%`}</strong></div></div>
    <div className="analytics-grid"><section className="panel analytics-panel"><div className="eyebrow">ENTITY LABELS</div><h3>Entity distribution</h3>{data.entity_types.length ? chart(data.entity_types) : <p className="muted-copy">No entities processed yet.</p>}</section><section className="panel analytics-panel"><div className="eyebrow">RELATION LABELS</div><h3>Fact distribution</h3>{data.predicates.length ? chart(data.predicates) : <p className="muted-copy">No facts processed yet.</p>}</section><section className="panel analytics-panel"><div className="eyebrow">HUMAN REVIEW</div><h3>Review outcomes</h3>{Object.entries(data.review_counts).length ? chart(Object.entries(data.review_counts).map(([label, count]) => ({ label, count }))) : <p className="muted-copy">Review a fact to establish this metric.</p>}</section><section className="panel analytics-panel eval-panel"><div className="eyebrow">EXTRACTION EVALUATION</div><h3>Precision, recall, and F1</h3>{evaluation?.available ? <><p>Evaluation runs against your JSONL test split with exact entity and relation span matching.</p>{Object.entries(evaluation.entity_metrics).map(([method, score]) => <div className="eval-score" key={`e-${method}`}><strong>Entities · {method}</strong><span>P {displayScore(score.precision)} · R {displayScore(score.recall)} · F1 {displayScore(score.f1)}</span></div>)}{Object.entries(evaluation.relation_metrics).map(([method, score]) => <div className="eval-score" key={`r-${method}`}><strong>Relations · {method}</strong><span>P {displayScore(score.precision)} · R {displayScore(score.recall)} · F1 {displayScore(score.f1)}</span></div>)}<p>Evidence grounding: {displayScore(evaluation.evidence_grounding_rate)}</p>{evaluation.warnings?.map((warning) => <small key={warning}>{warning}</small>)}</> : <><strong>Not measured</strong><p>{evaluation?.message || data.quality_metrics_note}</p><span>Use data/annotations/README.md and set PAPERGRAPH_ANNOTATIONS_PATH.</span></>}</section></div>
  </div>
}

export function SettingsPage({ onError, onNotice }) {
  const [settings, setSettings] = useState(null)
  const [merges, setMerges] = useState([])
  const [loading, setLoading] = useState(true)
  const load = async () => {
    try { const [config, log] = await Promise.all([getSettings(), getEntityMerges()]); setSettings(config); setMerges(log) }
    catch (error) { onError(error.response?.data?.detail || 'Could not load settings.') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  const undo = async (merge) => {
    try { await undoEntityMerge(merge.id); onNotice(`Merge of ${merge.source_label} was undone.`); await load() }
    catch (error) { onError(error.response?.data?.detail || 'Could not undo entity merge.') }
  }
  const sync = async () => { try { await syncGraph(); onNotice('Graph synchronization completed.') } catch (error) { onError(error.response?.data?.detail || 'Graph synchronization failed.') } }
  if (loading) return <div className="page-content"><Loading /></div>
  if (!settings) return <div className="page-content"><ErrorBox>Settings could not be loaded.</ErrorBox></div>
  return <div className="page-content knowledge-page"><Header eyebrow="WORKSPACE CONFIGURATION" title="Settings" subtitle="Edit .env.example into .env, fill in the values you need, then restart the backend." action={settings.graph_backend === 'neo4j' && <button className="button secondary" onClick={sync}><RefreshCw size={14} /> Sync Neo4j</button>} />
    <div className="settings-grid"><section className="panel settings-panel"><div className="eyebrow">STORAGE</div><h3>Data and graph</h3><SettingRow label="Local data folder" value={settings.data_dir} /><SettingRow label="Graph backend" value={settings.graph_backend} /><SettingRow label="Neo4j endpoint" value={settings.neo4j_uri || 'Not enabled'} /><SettingRow label="Neo4j password" value={settings.graph_backend === 'neo4j' ? (settings.neo4j_configured ? 'Configured (hidden)' : 'Missing') : 'Not required for SQLite'} /><SettingRow label="Max graph nodes" value={settings.graph_max_nodes} /><SettingRow label="CORS origins" value={settings.cors_origins.join(', ')} /></section>
      <section className="panel settings-panel"><div className="eyebrow">PROCESSING</div><h3>Extraction and retrieval</h3><SettingRow label="Maximum upload" value={`${(settings.max_upload_bytes / 1048576).toFixed(0)} MiB`} /><SettingRow label="Chunk size / overlap" value={`${settings.chunk_size_chars} / ${settings.chunk_overlap_chars} chars`} /><SettingRow label="Minimum heuristic score" value={settings.minimum_extraction_confidence} /><SettingRow label="Entity review threshold" value={settings.entity_review_threshold} /><SettingRow label="Embedding search" value={settings.embeddings_enabled ? settings.embedding_model : 'Disabled'} /><SettingRow label="LLM provider" value={settings.llm_provider} /><SettingRow label="LLM endpoint" value={settings.llm_base_url || 'Not enabled'} /><SettingRow label="LLM model" value={settings.llm_model || 'Not enabled'} /><SettingRow label="LLM API key" value={settings.llm_provider === 'none' ? 'Not required' : (settings.llm_api_key_configured ? 'Configured (hidden)' : 'Missing')} /><SettingRow label="Annotation file" value={settings.annotations_path} /></section></div>
      <section className="panel settings-panel env-note"><div><div className="eyebrow">LOCAL CONFIGURATION</div><h3>Fill in the environment template</h3><p>Copy <code>.env.example</code> to <code>.env</code> at the project root. Fill NEO4J_PASSWORD for Docker mode. To use Gemini, set <code>PAPERGRAPH_LLM_PROVIDER=gemini</code>, add your Gemini API key under <code>PAPERGRAPH_LLM_API_KEY</code>, and choose a model. Uploaded text will be sent to the selected provider. Restart the API after changes.</p></div><a className="button secondary" href="/.env.example" download>Get .env.example <Download size={14} /></a></section>
    <section className="panel settings-panel"><div className="eyebrow">ENTITY RESOLUTION AUDIT</div><h3>Recent manual merges</h3>{merges.length ? merges.map((merge) => <div className="merge-log-row" key={merge.id}><span><strong>{merge.source_label}</strong> → <strong>{merge.target_label}</strong><small>{merge.note || 'No note'} · {new Date(merge.created_at).toLocaleString()}</small></span><button className="button secondary" onClick={() => undo(merge)}><Undo2 size={14} /> Undo</button></div>) : <p className="muted-copy">Manual entity merges will be recorded here and can be undone.</p>}</section>
    <p className="settings-footnote">The API reports configuration values without returning secrets. Rule scores remain heuristic; quality metrics appear only after a labeled evaluation set is supplied.</p>
  </div>
}

function SettingRow({ label, value }) { return <div className="setting-row"><span>{label}</span><strong>{value ?? '—'}</strong></div> }
function displayScore(value) { return value == null ? '—' : `${(value * 100).toFixed(1)}%` }
