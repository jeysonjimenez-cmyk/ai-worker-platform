const BASE = import.meta.env.VITE_API_URL ?? ''

export function createClient(adminKey) {
  async function request(method, path, body) {
    const opts = {
      method,
      headers: { 'X-Admin-Key': adminKey },
    }
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json'
      opts.body = JSON.stringify(body)
    }
    const res = await fetch(BASE + path, opts)
    if (res.status === 401) throw new Error('unauthorized')
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || res.statusText)
    }
    if (res.status === 204) return null
    return res.json()
  }

  return {
    getWorkers: () => request('GET', '/admin/workers'),
    getJobs: (status) => request('GET', '/admin/jobs' + (status ? '?status=' + status : '')),
    getJob: (id) => request('GET', `/admin/jobs/${id}`),
    cancelJob: (id) => request('POST', `/admin/jobs/${id}/cancel`),
    retryJob: (id) => request('POST', `/admin/jobs/${id}/retry`),
  }
}
