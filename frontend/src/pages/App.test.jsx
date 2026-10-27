import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from './App.jsx'
import { askQuestion, getAnalytics, getDocuments, getEntities, getEntity, getEvaluation, getFacts, getGraph, getJobs, getStats, reviewFact, searchKnowledge } from '../api/client.js'

vi.mock('../api/client.js', () => ({
  getDocuments: vi.fn(),
  getJobs: vi.fn(),
  getStats: vi.fn(),
  getEntities: vi.fn(),
  getEntity: vi.fn(),
  getFacts: vi.fn(),
  getGraph: vi.fn(),
  searchKnowledge: vi.fn(),
  askQuestion: vi.fn(),
  getAnalytics: vi.fn(),
  getEvaluation: vi.fn(),
  getSettings: vi.fn(),
  getEntityCandidates: vi.fn(),
  getEntityMerges: vi.fn(),
  shortestPath: vi.fn(),
  getNeighbors: vi.fn(),
  exportGraph: vi.fn(),
  mergeEntities: vi.fn(),
  undoEntityMerge: vi.fn(),
  syncGraph: vi.fn(),
  getSchema: vi.fn(),
  createTextDocument: vi.fn(),
  deleteFact: vi.fn(),
  reviewFact: vi.fn(),
  uploadDocument: vi.fn(),
  getDocument: vi.fn(),
  runDocumentExtraction: vi.fn(),
  deleteDocument: vi.fn(),
  retryJob: vi.fn(),
}))

describe('PaperGraph workspace', () => {
  beforeEach(() => {
    getDocuments.mockResolvedValue({
      items: [{
        id: 'doc-1', filename: 'research-note.txt', extension: '.txt', content_type: 'text/plain',
        size_bytes: 128, sha256: 'abc123', status: 'completed', error_message: null,
        created_at: '2026-10-27T08:00:00+00:00', processed_at: '2026-10-27T08:00:01+00:00',
        page_count: 0, chunk_count: 2, job_id: 'job-1', duplicate_upload: false,
      }],
      total: 1,
    })
    getJobs.mockResolvedValue([])
    getStats.mockResolvedValue({ document_count: 1, entity_count: 2, fact_count: 1, pending_fact_count: 1 })
    getEntities.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 })
    getGraph.mockResolvedValue({ nodes: [], edges: [], total_entities: 0, total_facts: 0, truncated: false })
    getAnalytics.mockResolvedValue({ entity_types: [], predicates: [], review_counts: {}, review_acceptance_rate: null, average_processing_seconds: null, entities_with_aliases: 0 })
    getEvaluation.mockResolvedValue({ available: false, message: 'No labeled test data.' })
    searchKnowledge.mockResolvedValue({ results: [], retrieval_mode: 'lexical' })
    askQuestion.mockResolvedValue({ question: 'What is connected?', answer: 'No supporting evidence.', insufficient_evidence: true, citations: [] })
    getFacts.mockResolvedValue({
      items: [{
        id: 'fact-1', subject: { id: 'entity-1', label: 'Evidence Linking', entity_type: 'Method' },
        predicate: 'RELATED_TO', polarity: 'affirmed', object: { id: 'entity-2', label: 'Claim', entity_type: 'Concept' },
        extraction_method: 'rule_based', model_version: 'rule-baseline/0.1', confidence: 0.7,
        review_status: 'pending', created_at: '2026-10-27T08:00:00+00:00',
        evidence: [{ document_id: 'doc-1', filename: 'research-note.txt', chunk_index: 0,
          evidence_text: 'Evidence Linking connects a Claim.', start_offset: 0, end_offset: 34,
          location_type: 'paragraph', location_label: 'Paragraph 1', extraction_method: 'rule_based',
          model_version: 'rule-baseline/0.1', confidence: 0.7, created_at: '2026-10-27T08:00:00+00:00' }],
      }], total: 1, limit: 100, offset: 0,
    })
    reviewFact.mockResolvedValue({ review_status: 'approved' })
  })

  it('renders counts and recent source data from the API', async () => {
    render(<MemoryRouter><App /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Good to see you.' })).toBeInTheDocument()
    expect(screen.getByText('research-note.txt')).toBeInTheDocument()
    expect(screen.getByText('Recently added')).toBeInTheDocument()
    expect(screen.getByText('Candidate facts')).toBeInTheDocument()
    expect(screen.getByText('SQLite projection; optional Neo4j mirror')).toBeInTheDocument()
  })

  it('shows source evidence and sends a review decision', async () => {
    render(<MemoryRouter><App /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('link', { name: /Extracted facts/ }))

    expect(await screen.findByText('Evidence Linking')).toBeInTheDocument()
    expect(screen.getByText(/Evidence Linking connects a Claim\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Approve fact/ }))
    expect(await screen.findByText('Fact marked approved.')).toBeInTheDocument()
    expect(reviewFact).toHaveBeenCalledWith('fact-1', 'approved')
  })

  it('renders the graph as a tree and shows detailed source mentions for a selected entity', async () => {
    getEntities.mockResolvedValue({ items: [{ id: 'e1', label: 'Evidence Linking', entity_type: 'Method' }], total: 1 })
    getGraph.mockResolvedValue({
      nodes: [{ id: 'e1', label: 'Evidence Linking', entity_type: 'Method', aliases: [], mention_count: 1 }, { id: 'e2', label: 'Claim', entity_type: 'Concept', aliases: [], mention_count: 1 }],
      edges: [{ id: 'f1', subject: { id: 'e1', label: 'Evidence Linking' }, object: { id: 'e2', label: 'Claim' }, predicate: 'RELATED_TO', polarity: 'affirmed', review_status: 'pending', evidence: [] }],
      total_entities: 2, total_facts: 1, truncated: false,
    })
    getEntity.mockResolvedValue({
      id: 'e1', label: 'Evidence Linking', normalized_name: 'evidence linking', entity_type: 'Method', aliases: [], mention_count: 1,
      mentions: [{ document_id: 'doc-1', filename: 'research-note.txt', location_label: 'Paragraph 1', surface_form: 'Evidence Linking', start_offset: 0, end_offset: 16, extraction_method: 'rule_based', confidence: 0.9 }],
    })
    render(<MemoryRouter><App /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('link', { name: /Graph explorer/ }))
    expect(await screen.findByRole('heading', { name: 'Graph explorer' })).toBeInTheDocument()
    expect(await screen.findByRole('tree', { name: 'Knowledge graph tree' })).toBeInTheDocument()
    expect(screen.getByText('RELATED TO')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: 'Tree root entity' }), { target: { value: 'e1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Collapse Evidence Linking' }))
    expect(screen.queryByRole('button', { name: /Claim/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand Evidence Linking' }))
    fireEvent.click(screen.getByRole('button', { name: /^Method Evidence Linking/ }))
    expect(await screen.findByRole('heading', { name: /Source mentions/ })).toBeInTheDocument()
    expect(await screen.findByText('Paragraph 1')).toBeInTheDocument()
    expect(screen.getByText(/offsets 0–16/)).toBeInTheDocument()
  })

  it('searches source content and displays the configured retrieval result', async () => {
    searchKnowledge.mockResolvedValue({ query: 'Evidence Linking', retrieval_mode: 'lexical', results: [{ kind: 'passage', document_id: 'doc-1', filename: 'research-note.txt', location_label: 'Paragraph 1', text: 'Evidence Linking connects a Claim.', score: 1 }] })
    render(<MemoryRouter><App /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('link', { name: /^Search$/ }))
    fireEvent.change(await screen.findByPlaceholderText('Search your documents and graph'), { target: { value: 'Evidence Linking' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText(/Evidence Linking connects a Claim\./)).toBeInTheDocument()
  })
})
