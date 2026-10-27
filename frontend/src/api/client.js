import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000/api',
  timeout: 30000,
})

export const getDocuments = (params = {}) => api.get('/documents', { params }).then((response) => response.data)
export const getJobs = () => api.get('/jobs').then((response) => response.data)
export const getStats = () => api.get('/stats').then((response) => response.data)
export const getSchema = () => api.get('/schema').then((response) => response.data)
export const getEntities = (params = {}) => api.get('/entities', { params }).then((response) => response.data)
export const getEntity = (id) => api.get(`/entities/${id}`).then((response) => response.data)
export const getFacts = (params = {}) => api.get('/facts', { params }).then((response) => response.data)
export const reviewFact = (id, update) => api.patch(`/facts/${id}`, typeof update === 'string' ? { review_status: update } : update).then((response) => response.data)
export const deleteFact = (id) => api.delete(`/facts/${id}`)
export const uploadDocument = (file, onUploadProgress) => {
  const data = new FormData()
  data.append('file', file)
  return api.post('/documents', data, { onUploadProgress }).then((response) => response.data)
}
export const getDocument = (id) => api.get(`/documents/${id}`).then((response) => response.data)
export const runDocumentExtraction = (id) => api.post(`/documents/${id}/extract`).then((response) => response.data)
export const deleteDocument = (id) => api.delete(`/documents/${id}`)
export const retryJob = (id) => api.post(`/jobs/${id}/retry`).then((response) => response.data)
export const createTextDocument = (filename, text) => api.post('/documents/text', { filename, text }).then((response) => response.data)
export const getGraph = (params = {}) => api.get('/graphs/current', { params }).then((response) => response.data)
export const getNeighbors = (id, params = {}) => api.get(`/graphs/current/neighbors/${id}`, { params }).then((response) => response.data)
export const shortestPath = (params) => api.get('/graphs/current/shortest-path', { params }).then((response) => response.data)
export const exportGraph = (format, document_id) => api.get('/graphs/current/export', { params: { format, document_id }, responseType: 'blob' }).then((response) => response.data)
export const searchKnowledge = (query, limit = 10) => api.post('/search', { query, limit }).then((response) => response.data)
export const askQuestion = (question, limit = 8) => api.post('/qa', { question, limit }).then((response) => response.data)
export const getAnalytics = () => api.get('/analytics').then((response) => response.data)
export const getEvaluation = () => api.get('/evaluation').then((response) => response.data)
export const getSettings = () => api.get('/settings').then((response) => response.data)
export const getEntityCandidates = () => api.get('/entity-resolution/candidates').then((response) => response.data)
export const getEntityMerges = () => api.get('/entity-merges').then((response) => response.data)
export const mergeEntities = (source_entity_id, target_entity_id, note = '') => api.post('/entity-merges', { source_entity_id, target_entity_id, note }).then((response) => response.data)
export const undoEntityMerge = (id) => api.post(`/entity-merges/${id}/undo`).then((response) => response.data)
export const syncGraph = () => api.post('/graphs/sync').then((response) => response.data)
export default api
