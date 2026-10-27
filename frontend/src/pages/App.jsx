import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity, AlertCircle, ArrowDownToLine, ArrowRight, BookOpen, Check, ChevronDown,
  CircleHelp, Clock3, FilePlus2, FileText, Files, FolderOpen, Gauge, GitFork,
  LayoutDashboard, LoaderCircle, MoreHorizontal, Network, RefreshCw, Search,
  Settings2, ShieldCheck, Sparkles, Trash2, UploadCloud, X, ThumbsUp, ThumbsDown,
  Pencil, BarChart3, TextCursorInput, GitMerge,
} from 'lucide-react'
import { createTextDocument, deleteDocument, deleteFact, getDocument, getDocuments, getEntities, getEntity, getEntityCandidates, getFacts, getJobs, getSchema, getStats, mergeEntities, reviewFact, retryJob, runDocumentExtraction, uploadDocument } from '../api/client.js'
import { AnalyticsPage, AssistantPage, GraphPage, SearchPage, SettingsPage } from './KnowledgePages.jsx'

const navItems = [
  { label: 'Overview', to: '/', icon: LayoutDashboard },
  { label: 'Documents', to: '/documents', icon: Files },
  { label: 'Entities', to: '/entities', icon: Network },
  { label: 'Extracted facts', to: '/facts', icon: GitFork },
  { label: 'Graph explorer', to: '/graph', icon: Network },
  { label: 'Search', to: '/search', icon: Search },
  { label: 'Knowledge assistant', to: '/assistant', icon: Sparkles },
  { label: 'Analytics', to: '/analytics', icon: BarChart3 },
  { label: 'Settings', to: '/settings', icon: Settings2 },
]

const statusLabel = { queued: 'Queued', processing: 'Processing', completed: 'Extraction ready', failed: 'Failed' }

function App() {
  const [documents, setDocuments] = useState([])
  const [jobs, setJobs] = useState([])
  const [stats, setStats] = useState({ document_count: 0, entity_count: 0, fact_count: 0, pending_fact_count: 0 })
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [notice, setNotice] = useState('')
  const [dragging, setDragging] = useState(false)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteFilename, setPasteFilename] = useState('pasted-text.txt')
  const [pasteValue, setPasteValue] = useState('')
  const inputRef = useRef(null)
  const location = useLocation()
  const navigate = useNavigate()

  const refresh = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true)
    try {
      const [documentsResult, jobsResult, statsResult] = await Promise.all([getDocuments(), getJobs(), getStats()])
      setDocuments(documentsResult.items)
      setTotal(documentsResult.total)
      setJobs(jobsResult)
      setStats(statsResult)
      setError('')
    } catch {
      setError('Can’t reach the PaperGraph API. Start the backend and try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh(true)
    const timer = window.setInterval(() => refresh(false), 3500)
    return () => window.clearInterval(timer)
  }, [refresh])

  const filteredDocuments = useMemo(() => {
    const value = query.trim().toLowerCase()
    if (!value) return documents
    return documents.filter((document) => document.filename.toLowerCase().includes(value) || document.extension.includes(value))
  }, [documents, query])

  const handleUpload = async (files) => {
    const selectedFiles = Array.from(files || [])
    if (!selectedFiles.length) return
    setUploading(true)
    setUploadProgress(0)
    setNotice('')
    const completed = []
    const failures = []
    try {
      for (const file of selectedFiles) {
        try {
          const result = await uploadDocument(file, (event) => {
            const perFile = event.total ? (event.loaded / event.total) * 100 : 0
            setUploadProgress(Math.round(((completed.length + perFile / 100) / selectedFiles.length) * 100))
          })
          completed.push(result.duplicate_upload ? `${file.name} (already stored)` : file.name)
        } catch (uploadError) { failures.push(`${file.name}: ${uploadError.response?.data?.detail || 'upload failed'}`) }
      }
      if (completed.length) setNotice(`${completed.length} file${completed.length === 1 ? '' : 's'} added or already in the library.`)
      await refresh(false)
      if (failures.length) setError(failures.join(' · '))
      if (location.pathname !== '/documents') navigate('/documents')
    } finally {
      setUploading(false)
      setUploadProgress(0)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const submitPastedText = async (event) => {
    event.preventDefault()
    try {
      const result = await createTextDocument(pasteFilename, pasteValue)
      setNotice(result.duplicate_upload ? 'This exact text is already in your library.' : 'Pasted text added. Extraction is running.')
      setPasteOpen(false); setPasteValue(''); await refresh(false); navigate('/documents')
    } catch (pasteError) { setError(pasteError.response?.data?.detail || 'Could not add pasted text.') }
  }

  const openDocument = async (document) => {
    try {
      setSelected(await getDocument(document.id))
    } catch {
      setError('Could not load extracted text for this document.')
    }
  }

  const removeDocument = async (document) => {
    if (!window.confirm(`Delete “${document.filename}” and its extracted text?`)) return
    try {
      await deleteDocument(document.id)
      if (selected?.id === document.id) setSelected(null)
      setNotice(`${document.filename} deleted.`)
      await refresh(false)
    } catch {
      setError('Could not delete that document.')
    }
  }

  const retry = async (document) => {
    if (!document.job_id) return
    try {
      await retryJob(document.job_id)
      setNotice(`Retry started for ${document.filename}.`)
      await refresh(false)
    } catch (retryError) {
      setError(retryError.response?.data?.detail || 'Could not retry this job.')
    }
  }

  const reprocess = async (document) => {
    try {
      await runDocumentExtraction(document.id)
      setNotice(`Extraction queued for ${document.filename}.`)
      await refresh(false)
    } catch (extractError) {
      setError(extractError.response?.data?.detail || 'Could not queue extraction for this document.')
    }
  }

  return (
    <div className="app-shell" onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={(event) => { if (event.target === event.currentTarget) setDragging(false) }} onDrop={(event) => { event.preventDefault(); setDragging(false); handleUpload(event.dataTransfer.files) }}>
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark"><Network size={19} strokeWidth={2.4} /></span>
          <span><strong>papergraph</strong><small>AI WORKSPACE</small></span>
        </Link>
        <div className="workspace-switcher"><div className="workspace-avatar">P</div><span><strong>Personal workspace</strong><small>Local environment</small></span><ChevronDown size={15} /></div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="side-nav">
          {navItems.map(({ label, to, icon: Icon, soon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''} ${soon ? 'muted' : ''}`}>
              <Icon size={17} /><span>{label}</span>{soon && <span className="soon-badge">SOON</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="milestone-card">
          <div className="milestone-icon"><Activity size={15} /></div>
          <div><strong>Core workflow ready</strong><p>Sources, graph, evidence and review</p></div>
          <span className="live-dot" />
        </div>
        <div className="sidebar-footer"><div className="user-avatar">S</div><div><strong>Local user</strong><small>Academic project</small></div><MoreHorizontal size={17} /></div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><span className="crumb-divider">/</span><strong>{location.pathname === '/' ? 'Overview' : location.pathname.slice(1).replace('-', ' ')}</strong></div>
          <div className="topbar-actions"><span className="api-status"><span className={`live-dot ${error ? 'offline' : ''}`} />{error ? 'API unavailable' : 'API connected'}</span><button className="icon-button" title="Settings" onClick={() => navigate('/settings')}><Settings2 size={18} /></button><button className="help-button"><CircleHelp size={16} /> Help</button></div>
        </header>

        {error && <div className="alert error-alert"><AlertCircle size={17} /><span>{error}</span><button onClick={() => { setError(''); refresh(false) }} aria-label="Dismiss"><X size={16} /></button></div>}
        {notice && <div className="alert success-alert"><Check size={17} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Dismiss"><X size={16} /></button></div>}

        <Routes>
          <Route path="/" element={<Overview documents={documents} jobs={jobs} stats={stats} total={total} loading={loading} onUpload={() => inputRef.current?.click()} onOpen={openDocument} />} />
          <Route path="/documents" element={<DocumentLibrary documents={filteredDocuments} total={total} loading={loading} query={query} setQuery={setQuery} onUpload={() => inputRef.current?.click()} onPaste={() => setPasteOpen(true)} onOpen={openDocument} onDelete={removeDocument} onRetry={retry} onExtract={reprocess} uploading={uploading} progress={uploadProgress} />} />
          <Route path="/entities" element={<EntitiesPage onError={setError} />} />
          <Route path="/facts" element={<FactsPage onError={setError} onNotice={setNotice} onRefresh={refresh} />} />
          <Route path="/graph" element={<GraphPage onError={setError} onNotice={setNotice} />} />
          <Route path="/search" element={<SearchPage onError={setError} />} />
          <Route path="/assistant" element={<AssistantPage onError={setError} />} />
          <Route path="/analytics" element={<AnalyticsPage onError={setError} />} />
          <Route path="/settings" element={<SettingsPage onError={setError} onNotice={setNotice} />} />
          <Route path="*" element={<ComingSoon />} />
        </Routes>
      </main>

      <input ref={inputRef} type="file" multiple accept=".pdf,.docx,.pptx,.txt,.md,.markdown" className="hidden-input" onChange={(event) => handleUpload(event.target.files)} />
      {dragging && <div className="drop-overlay"><div className="drop-card"><UploadCloud size={35} /><strong>Drop to add to your library</strong><span>PDF, DOCX, PPTX, TXT or Markdown</span></div></div>}
      {selected && <DocumentDrawer document={selected} onClose={() => setSelected(null)} />}
      {pasteOpen && <div className="drawer-backdrop paste-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setPasteOpen(false) }}><form className="paste-modal panel" onSubmit={submitPastedText}><div className="drawer-head"><div><div className="eyebrow">NEW SOURCE</div><h3>Paste text</h3></div><button type="button" className="icon-button" onClick={() => setPasteOpen(false)}><X size={18} /></button></div><label>Document name<input value={pasteFilename} onChange={(event) => setPasteFilename(event.target.value)} required /></label><label>Text<textarea value={pasteValue} onChange={(event) => setPasteValue(event.target.value)} rows={12} required placeholder="Paste article, notes, or research text here…" /></label><div className="paste-actions"><span>{new Blob([pasteValue]).size.toLocaleString()} bytes</span><button className="button primary" disabled={!pasteValue.trim()}>Add to library</button></div></form></div>}
    </div>
  )
}

function PageHeader({ eyebrow, title, subtitle, action }) {
  return <div className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>
}

function Overview({ documents, jobs, stats, total, loading, onUpload, onOpen }) {
  const latest = documents.slice(0, 5)
  const runningJobs = jobs.filter((job) => ['queued', 'processing'].includes(job.status)).length
  return <div className="page-content">
    <PageHeader eyebrow="YOUR RESEARCH, CONNECTED" title="Good to see you." subtitle="Build a living knowledge graph from the documents you trust." action={<button className="button primary" onClick={onUpload}><FilePlus2 size={16} /> Add documents</button>} />
    <section className="welcome-strip"><div className="welcome-art"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit-core"><Network size={25} /></div><span className="orbit-node node-one" /><span className="orbit-node node-two" /><span className="orbit-node node-three" /></div><div className="welcome-copy"><div className="welcome-tag"><Sparkles size={13} /> YOUR KNOWLEDGE, IN CONTEXT</div><h2>Every idea has a source.<br /><em>Every connection has a reason.</em></h2><p>Upload a paper or report to begin building an evidence-backed knowledge graph across your collection.</p><button className="text-button" onClick={onUpload}>Upload your first document <ArrowRight size={15} /></button></div><div className="welcome-counter"><span>{String(total).padStart(2, '0')}</span><small>documents<br />in your library</small></div></section>
    <div className="stats-grid">
      <StatCard icon={Files} label="Documents" value={total} foot="Across your workspace" tone="mint" />
      <StatCard icon={Network} label="Entities" value={stats.entity_count} foot="Exact names and aliases" tone="blue" />
      <StatCard icon={GitFork} label="Candidate facts" value={stats.fact_count} foot="With source evidence" tone="mint" />
      <StatCard icon={Clock3} label="Needs review" value={stats.pending_fact_count} foot={`${runningJobs} processing job${runningJobs === 1 ? '' : 's'}`} tone="amber" />
    </div>
    <div className="overview-grid">
      <section className="panel recent-panel"><div className="panel-heading"><div><div className="eyebrow">COLLECTION</div><h3>Recently added</h3></div><Link to="/documents" className="small-link">View library <ArrowRight size={14} /></Link></div>
        {loading ? <Loading /> : latest.length ? <div className="recent-list">{latest.map((document) => <DocumentRow key={document.id} document={document} onOpen={onOpen} compact />)}</div> : <EmptyState title="Your library is ready" text="Add a document to start extracting source text." icon={FolderOpen} />}
      </section>
      <section className="panel pipeline-panel"><div className="panel-heading"><div><div className="eyebrow">PIPELINE</div><h3>From document to graph</h3></div><span className="pipeline-status"><span className="live-dot" /> ACTIVE</span></div>
        <div className="pipeline-steps"><PipelineStep icon={UploadCloud} title="Ingest source" detail="PDF · DOCX · PPTX · TXT · pasted text" state="done" /><div className="pipeline-connector complete" /><PipelineStep icon={FileText} title="Extract text" detail="Keep page and section context" state="done" /><div className="pipeline-connector complete" /><PipelineStep icon={GitFork} title="Identify entities and facts" detail="Evidence linked; human review" state="done" /><div className="pipeline-connector complete" /><PipelineStep icon={Network} title="Explore knowledge graph" detail="SQLite projection; optional Neo4j mirror" state="current" /></div>
        <div className="pipeline-note"><ShieldCheck size={15} /><span>Source files and extracted text stay in your local workspace.</span></div>
      </section>
    </div>
  </div>
}

function StatCard({ icon: Icon, label, value, foot, tone }) {
  return <article className="stat-card"><div className={`stat-icon ${tone}`}><Icon size={17} /></div><div className="stat-label">{label}</div><div className="stat-value">{value}</div><div className="stat-foot">{foot}</div></article>
}

function PipelineStep({ icon: Icon, title, detail, state }) {
  return <div className={`pipeline-step ${state}`}><div className="step-icon"><Icon size={16} /></div><div><strong>{title}</strong><span>{detail}</span></div>{state === 'done' ? <Check className="step-check" size={15} /> : state === 'current' ? <span className="step-live" /> : null}</div>
}

function DocumentLibrary({ documents, total, loading, query, setQuery, onUpload, onPaste, onOpen, onDelete, onRetry, onExtract, uploading, progress }) {
  return <div className="page-content library-page">
    <PageHeader eyebrow="SOURCE MATERIAL" title="Document library" subtitle="Manage documents and inspect the text extracted from each source." action={<div className="header-actions"><button className="button secondary" onClick={onPaste}><TextCursorInput size={15} /> Paste text</button><button className="button primary" onClick={onUpload}><FilePlus2 size={16} /> Upload documents</button></div>} />
    <div className="library-summary"><div className="summary-icon"><BookOpen size={18} /></div><div><strong>{total} {total === 1 ? 'document' : 'documents'}</strong><span>Duplicate files are detected by SHA-256 and won’t be stored twice.</span></div><button className="button secondary" onClick={onUpload}><UploadCloud size={16} /> Add files</button></div>
    {uploading && <div className="upload-progress"><span>Uploading your file…</span><div className="progress-track"><div style={{ width: `${progress}%` }} /></div><strong>{progress}%</strong></div>}
    <section className="panel document-panel"><div className="document-toolbar"><div><div className="eyebrow">ALL SOURCES</div><h3>Documents <span>{total}</span></h3></div><div className="toolbar-actions"><div className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search documents" /></div><button className="button subtle"><ChevronDown size={15} /> Recently added</button></div></div>
      <div className="table-head"><span>DOCUMENT</span><span>SIZE</span><span>STATUS</span><span>ADDED</span><span /></div>
      {loading ? <Loading /> : documents.length ? <div className="document-table">{documents.map((document) => <DocumentRow key={document.id} document={document} onOpen={onOpen} onDelete={onDelete} onRetry={onRetry} onExtract={onExtract} />)}</div> : <EmptyState title={query ? 'No matching documents' : 'No documents yet'} text={query ? 'Try another file name or clear your search.' : 'Upload a PDF, Word document, presentation, text file or Markdown note.'} icon={FolderOpen} action={!query && <button className="button secondary" onClick={onUpload}><UploadCloud size={15} /> Choose a file</button>} />}
      <div className="panel-footer"><span>Showing {documents.length} of {total}</span><span><ShieldCheck size={14} /> Locally stored</span></div>
    </section>
    <div className="format-note"><div className="format-icon"><CircleHelp size={16} /></div><div><strong>Scanned PDFs?</strong><span>This milestone extracts selectable text. OCR support is planned as an optional extension.</span></div><button className="text-button">Learn more <ArrowRight size={14} /></button></div>
  </div>
}

function DocumentRow({ document, onOpen, onDelete, onRetry, onExtract, compact = false }) {
  const extension = document.extension.replace('.', '').toUpperCase()
  const iconTone = extension === 'PDF' ? 'pdf' : ['DOCX'].includes(extension) ? 'word' : ['PPTX'].includes(extension) ? 'slides' : 'plain'
  return <div className={`document-row ${compact ? 'compact-row' : ''}`}>
    <button className="document-cell" onClick={() => onOpen(document)}><span className={`file-icon ${iconTone}`}><FileText size={17} /><small>{extension.slice(0, 4)}</small></span><span className="file-copy"><strong>{document.filename}</strong><small>{document.chunk_count ? `${document.chunk_count} text chunks` : document.status === 'completed' ? 'No extracted chunks' : 'Source document'}</small></span></button>
    {!compact && <span className="size-cell">{formatBytes(document.size_bytes)}</span>}
    <span className={`status-pill ${document.status}`}><span />{statusLabel[document.status]}</span>
    {!compact && <span className="date-cell">{formatDate(document.created_at)}</span>}
    <div className="row-actions"><button className="icon-button small" onClick={() => onOpen(document)} title="View extracted text"><ArrowDownToLine size={15} /></button>{document.status === 'failed' && <button className="icon-button small" onClick={() => onRetry(document)} title="Retry"><RefreshCw size={15} /></button>}{document.status === 'completed' && onExtract && <button className="icon-button small" onClick={() => onExtract(document)} title="Run extraction again"><GitFork size={15} /></button>}{onDelete && <button className="icon-button small danger-icon" onClick={() => onDelete(document)} title="Delete document"><Trash2 size={15} /></button>}</div>
  </div>
}

function DocumentDrawer({ document, onClose }) {
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><aside className="document-drawer"><div className="drawer-head"><div><div className="eyebrow">SOURCE PREVIEW</div><h3>Extracted text</h3></div><button className="icon-button" onClick={onClose}><X size={18} /></button></div><div className="drawer-file"><span className="file-icon plain"><FileText size={17} /></span><div><strong>{document.filename}</strong><span>{formatBytes(document.size_bytes)} · {document.chunk_count} chunks</span></div></div><div className="drawer-status"><span className={`status-pill ${document.status}`}><span />{statusLabel[document.status]}</span>{document.error_message && <p className="error-copy">{document.error_message}</p>}</div><div className="chunk-list">{document.chunks?.length ? document.chunks.map((chunk, index) => <article className="chunk-card" key={`${index}-${chunk.start_offset}`}><div className="chunk-meta"><span><FileText size={13} /> {chunk.location_label}</span><span>Chars {chunk.start_offset}–{chunk.end_offset}</span></div>{chunk.section && <div className="section-label">{chunk.section}</div>}<p>{chunk.text}</p></article>) : <EmptyState title="Text isn’t available yet" text="When extraction finishes, source text and its location will appear here." icon={FileText} />}</div><div className="drawer-footer"><ShieldCheck size={15} /><span>Text was extracted locally and remains linked to this source.</span></div></aside></div>
}

function EntitiesPage({ onError }) {
  const [entities, setEntities] = useState([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [refreshVersion, setRefreshVersion] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    getEntities({ q: query.trim() || undefined, limit: 100 })
      .then((result) => { if (active) { setEntities(result.items); setTotal(result.total) } })
      .catch(() => { if (active) onError('Could not load extracted entities.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query, onError, refreshVersion])

  const openEntity = async (entity) => {
    try { setSelected(await getEntity(entity.id)) }
    catch { onError('Could not load entity source mentions.') }
  }

  return <div className="page-content library-page">
    <PageHeader eyebrow="CANONICAL ENTITIES" title="Entities" subtitle="Review names, exact-match aliases, and the source mentions behind each entity." />
    <EntityResolutionReview onError={onError} onNotice={() => setRefreshVersion((value) => value + 1)} />
    <section className="panel document-panel">
      <div className="document-toolbar"><div><div className="eyebrow">EXTRACTED CANDIDATES</div><h3>Entities <span>{total}</span></h3></div><div className="toolbar-actions"><div className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search labels and aliases" /></div></div></div>
      <div className="entity-table-head"><span>ENTITY</span><span>TYPE</span><span>ALIASES</span><span>MENTIONS</span></div>
      {loading ? <Loading /> : entities.length ? <div className="entity-table">{entities.map((entity) => <button className="entity-row" key={entity.id} onClick={() => openEntity(entity)}><span><strong>{entity.label}</strong><small>{entity.normalized_name}</small></span><span className="entity-type">{entity.entity_type}</span><span className="entity-aliases">{entity.aliases.length ? entity.aliases.join(', ') : '—'}</span><span className="mention-count">{entity.mention_count}</span></button>)}</div> : <EmptyState title="No entities extracted yet" text="Upload a document with named concepts or methods. The first pass uses explicit text patterns and keeps each source mention." icon={Network} />}
      <div className="panel-footer"><span>Showing {entities.length} of {total}</span><span><ShieldCheck size={14} /> Rule-based candidates</span></div>
    </section>
    {selected && <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null) }}><aside className="document-drawer"><div className="drawer-head"><div><div className="eyebrow">ENTITY PROFILE</div><h3>{selected.label}</h3></div><button className="icon-button" onClick={() => setSelected(null)}><X size={18} /></button></div><div className="entity-profile-tags"><span className="entity-type">{selected.entity_type}</span>{selected.aliases.map((alias) => <span className="alias-tag" key={alias}>{alias}</span>)}</div><div className="chunk-list entity-mentions">{selected.mentions.length ? selected.mentions.map((mention, index) => <article className="chunk-card" key={`${mention.document_id}-${mention.start_offset}-${index}`}><div className="chunk-meta"><span><FileText size={13} /> {mention.filename}</span><span>{mention.location_label}</span></div><p>“{mention.surface_form}” <span className="mention-offset">chars {mention.start_offset}–{mention.end_offset}</span></p><div className="mention-method">{mention.extraction_method} · {mention.model_version} · heuristic score {mention.confidence.toFixed(2)}</div></article>) : <EmptyState title="No source mentions" text="The entity may have been removed with its source document." icon={FileText} />}</div><div className="drawer-footer"><ShieldCheck size={15} /><span>Entity links use normalized exact matches and explicit aliases.</span></div></aside></div>}
  </div>
}

function EntityResolutionReview({ onError, onNotice }) {
  const [candidates, setCandidates] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const load = async () => {
    setLoading(true)
    try { const response = await getEntityCandidates(); setCandidates(response.items) }
    catch (error) { onError(error.response?.data?.detail || 'Could not load entity-resolution candidates.') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  const merge = async (source, target) => {
    const key = `${source.id}-${target.id}`
    if (!window.confirm(`Merge “${source.label}” into “${target.label}”? This can be undone from Settings.`)) return
    setBusy(key)
    try { await mergeEntities(source.id, target.id, 'Human-reviewed possible duplicate.'); onNotice(); await load() }
    catch (error) { onError(error.response?.data?.detail || 'Could not merge entities.') }
    finally { setBusy('') }
  }
  return <section className="panel resolution-panel"><div className="document-toolbar"><div><div className="eyebrow">HUMAN-IN-THE-LOOP RESOLUTION</div><h3>Possible duplicate entities <span>{candidates.length}</span></h3></div><span className="muted-copy">No automatic merges</span></div>
    {loading ? <Loading /> : candidates.length ? <div className="resolution-list">{candidates.map((item) => <div className="resolution-row" key={`${item.left.id}-${item.right.id}`}><div className="resolution-entities"><span><strong>{item.left.label}</strong><small>{item.left.entity_type} · {item.left.mention_count} mentions</small></span><b>↔</b><span><strong>{item.right.label}</strong><small>{item.right.entity_type} · {item.right.mention_count} mentions</small></span></div><div className="resolution-meta"><span>{item.review_level.replace('_', ' ')} · {Math.round(item.similarity * 100)}% · {item.similarity_method}</span><div><button className="button subtle" disabled={Boolean(busy)} onClick={() => merge(item.right, item.left)}>{busy === `${item.right.id}-${item.left.id}` ? 'Merging…' : `Keep ${item.left.label}`}</button><button className="button subtle" disabled={Boolean(busy)} onClick={() => merge(item.left, item.right)}>{busy === `${item.left.id}-${item.right.id}` ? 'Merging…' : `Keep ${item.right.label}`}</button></div></div></div>)}</div> : <EmptyState title="No likely duplicates found" text="New candidates appear as your collection grows. Similarity is advisory and never triggers an automatic merge." icon={GitMerge} />}
    <div className="panel-footer"><span>Entity types and review thresholds come from .env</span><span>Every merge is reversible from Settings</span></div>
  </section>
}

function FactsPage({ onError, onNotice, onRefresh }) {
  const [facts, setFacts] = useState([])
  const [total, setTotal] = useState(0)
  const [filter, setFilter] = useState('pending')
  const [loading, setLoading] = useState(true)
  const [editingFact, setEditingFact] = useState(null)
  const [entityOptions, setEntityOptions] = useState([])
  const [predicateOptions, setPredicateOptions] = useState([])
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const result = await getFacts({ review_status: filter === 'all' ? undefined : filter, limit: 100 })
      setFacts(result.items)
      setTotal(result.total)
    } catch {
      onError('Could not load extracted facts.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [filter])

  const setReview = async (fact, review_status) => {
    try {
      await reviewFact(fact.id, review_status)
      onNotice(`Fact marked ${review_status}.`)
      await Promise.all([load(), onRefresh(false)])
    } catch (reviewError) {
      onError(reviewError.response?.data?.detail || 'Could not update fact review status.')
    }
  }

  const editFact = async (fact) => {
    try {
      const [result, schema] = await Promise.all([getEntities({ limit: 200 }), getSchema()])
      setEntityOptions(result.items)
      setPredicateOptions(schema.relationship_types)
      setEditingFact({ fact, subject_entity_id: fact.subject.id, predicate: fact.predicate, object_entity_id: fact.object.id, polarity: fact.polarity, correction_note: '' })
    } catch { onError('Could not load entities for fact correction.') }
  }

  const saveCorrection = async (event) => {
    event.preventDefault()
    if (!editingFact) return
    setSaving(true)
    try {
      const { fact, ...update } = editingFact
      await reviewFact(fact.id, { ...update, review_status: fact.review_status })
      onNotice('Fact correction saved with an audit record.')
      setEditingFact(null)
      await Promise.all([load(), onRefresh(false)])
    } catch (error) { onError(error.response?.data?.detail || 'Could not save fact correction.') }
    finally { setSaving(false) }
  }

  const removeFact = async (fact) => {
    if (!window.confirm(`Delete the fact “${fact.subject.label} ${fact.predicate} ${fact.object.label}”?`)) return
    try { await deleteFact(fact.id); onNotice('Fact deleted.'); await Promise.all([load(), onRefresh(false)]) }
    catch (error) { onError(error.response?.data?.detail || 'Could not delete fact.') }
  }

  return <div className="page-content library-page">
    <PageHeader eyebrow="EVIDENCE REVIEW" title="Extracted facts" subtitle="Each candidate relation keeps its source passage, location, extraction method, and review status." />
    <section className="panel facts-panel">
      <div className="document-toolbar"><div><div className="eyebrow">SOURCE-BACKED RELATIONS</div><h3>Fact review <span>{total}</span></h3></div><div className="toolbar-actions"><select className="filter-select" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="pending">Needs review</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="all">All statuses</option></select></div></div>
      {loading ? <Loading /> : facts.length ? <div className="fact-list">{facts.map((fact) => <article className="fact-card" key={fact.id}><div className="fact-card-head"><div className="fact-assertion"><strong>{fact.subject.label}</strong><span className="predicate-chip">{fact.predicate.replaceAll('_', ' ')}</span>{fact.polarity === 'negated' && <span className="polarity-chip">NEGATED</span>}<strong>{fact.object.label}</strong></div><span className={`review-tag ${fact.review_status}`}>{fact.review_status.replace('_', ' ')}</span></div><div className="fact-meta"><span>{fact.subject.entity_type} → {fact.object.entity_type}</span><span>{fact.extraction_method} · {fact.model_version}</span><span>Heuristic score {fact.confidence.toFixed(2)} · uncalibrated</span></div>
        <div className="evidence-list">{fact.evidence.map((evidence, index) => <div className="evidence-block" key={`${evidence.document_id}-${evidence.start_offset}-${index}`}><div className="evidence-source"><FileText size={13} /><strong>{evidence.filename}</strong><span>{evidence.location_label}</span><span>chars {evidence.start_offset}–{evidence.end_offset}</span></div><p>“{evidence.evidence_text}”</p></div>)}</div>
        {fact.corrections?.length > 0 && <div className="correction-history">{fact.corrections.map((item) => <div key={item.id}>Correction: {item.note}</div>)}</div>}
        <div className="fact-actions"><button className="button subtle" onClick={() => editFact(fact)}><Pencil size={13} /> Correct</button><button className="icon-button" title="Delete fact" onClick={() => removeFact(fact)}><Trash2 size={14} /></button>{fact.review_status === 'pending' && <><button className="button secondary reject-button" onClick={() => setReview(fact, 'rejected')}><ThumbsDown size={14} /> Reject</button><button className="button primary approve-button" onClick={() => setReview(fact, 'approved')}><ThumbsUp size={14} /> Approve fact</button></>}</div>
      </article>)}</div> : <EmptyState title={filter === 'pending' ? 'No facts need review' : 'No facts found'} text="The baseline only creates a relation when it can link two extracted entity mentions to an explicit pattern and supporting text." icon={GitFork} />}
      <div className="panel-footer"><span>Showing {facts.length} of {total}</span><span><ShieldCheck size={14} /> Evidence required</span></div>
    </section>
    <div className="format-note"><div className="format-icon"><CircleHelp size={16} /></div><div><strong>Baseline scores are heuristic</strong><span>They are rule-match indicators, not calibrated probabilities. Use review status to validate candidate facts.</span></div></div>
    {editingFact && <div className="drawer-backdrop paste-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingFact(null) }}><form className="correction-modal panel" onSubmit={saveCorrection}><div className="drawer-head"><div><div className="eyebrow">FACT REVIEW</div><h3>Correct extracted fact</h3></div><button type="button" className="icon-button" onClick={() => setEditingFact(null)}><X size={18} /></button></div><label>Subject<select value={editingFact.subject_entity_id} onChange={(event) => setEditingFact({ ...editingFact, subject_entity_id: event.target.value })}>{entityOptions.map((entity) => <option key={entity.id} value={entity.id}>{entity.label} · {entity.entity_type}</option>)}</select></label><label>Relationship<select value={editingFact.predicate} onChange={(event) => setEditingFact({ ...editingFact, predicate: event.target.value })}>{predicateOptions.map((item) => <option key={item}>{item}</option>)}</select></label><label>Object<select value={editingFact.object_entity_id} onChange={(event) => setEditingFact({ ...editingFact, object_entity_id: event.target.value })}>{entityOptions.map((entity) => <option key={entity.id} value={entity.id}>{entity.label} · {entity.entity_type}</option>)}</select></label><label>Polarity<select value={editingFact.polarity} onChange={(event) => setEditingFact({ ...editingFact, polarity: event.target.value })}><option value="affirmed">Affirmed</option><option value="negated">Negated</option></select></label><label>Correction note<textarea value={editingFact.correction_note} onChange={(event) => setEditingFact({ ...editingFact, correction_note: event.target.value })} rows={3} placeholder="Why is this correction needed?" /></label><div className="paste-actions"><span>Original source evidence is retained in the audit trail.</span><button className="button primary" disabled={saving}>{saving ? 'Saving…' : 'Save correction'}</button></div></form></div>}
  </div>
}

function ComingSoon() {
  return <div className="page-content coming-page"><div className="coming-card"><span className="coming-icon"><GitFork size={22} /></span><div className="eyebrow">NEXT MILESTONE</div><h1>Knowledge graph tools are on the way.</h1><p>Document ingestion and evidence-linked extraction are in place. Neo4j graph exploration and grounded question answering will build on that foundation.</p><Link to="/documents" className="button secondary">Open document library <ArrowRight size={15} /></Link></div></div>
}

function Loading() { return <div className="loading-state"><LoaderCircle size={18} className="spin" /> Loading workspace data…</div> }
function EmptyState({ title, text, icon: Icon, action }) { return <div className="empty-state"><span><Icon size={21} /></span><strong>{title}</strong><p>{text}</p>{action}</div> }
function formatBytes(bytes) { if (!bytes) return '0 B'; const units = ['B', 'KB', 'MB', 'GB']; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1); return `${(bytes / (1024 ** index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}` }
function formatDate(value) { if (!value) return '—'; return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value)) }

export default App
