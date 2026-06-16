import { useState, useEffect, useCallback } from 'react'
import { createClient } from './api.js'

const SESSION_KEY = 'admin_key'

export default function App() {
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem(SESSION_KEY) ?? '')
  const [keyInput, setKeyInput] = useState('')
  const [authError, setAuthError] = useState(false)
  const [client, setClient] = useState(null)

  useEffect(() => {
    if (adminKey) {
      setClient(createClient(adminKey))
      setAuthError(false)
    } else {
      setClient(null)
    }
  }, [adminKey])

  function handleLogin(e) {
    e.preventDefault()
    const key = keyInput.trim()
    if (!key) return
    sessionStorage.setItem(SESSION_KEY, key)
    setAdminKey(key)
  }

  function handleUnauthorized() {
    sessionStorage.removeItem(SESSION_KEY)
    setAdminKey('')
    setKeyInput('')
    setAuthError(true)
  }

  if (!client) {
    return (
      <div>
        <h1>AI Worker Platform</h1>
        {authError && <p style={{ color: 'red' }}>No autorizado — verifica la admin key.</p>}
        <form onSubmit={handleLogin}>
          <label>
            Admin Key{' '}
            <input
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              autoFocus
            />
          </label>
          {' '}
          <button type="submit">Entrar</button>
        </form>
      </div>
    )
  }

  return <Dashboard client={client} onUnauthorized={handleUnauthorized} />
}

// T5.5 — workers + jobs view with 5s polling
function Dashboard({ client, onUnauthorized }) {
  const [workers, setWorkers] = useState([])
  const [jobs, setJobs] = useState([])
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [detailVersion, setDetailVersion] = useState(0)
  const [fetchError, setFetchError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const [ws, js] = await Promise.all([
        client.getWorkers(),
        client.getJobs(statusFilter),
      ])
      setWorkers(ws)
      setJobs(js)
      setFetchError(null)
    } catch (err) {
      if (err.message === 'unauthorized') {
        onUnauthorized()
      } else {
        setFetchError(err.message)
      }
    }
  }, [client, statusFilter, onUnauthorized])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 5000)
    return () => clearInterval(id)
  }, [fetchData])

  function handleAction() {
    fetchData()
    setDetailVersion((v) => v + 1)
  }

  return (
    <div>
      <h1>AI Worker Platform</h1>
      {fetchError && <p style={{ color: 'red' }}>Error al cargar: {fetchError}</p>}

      <WorkersPanel workers={workers} />

      <JobsPanel
        jobs={jobs}
        statusFilter={statusFilter}
        onStatusFilter={(s) => { setStatusFilter(s); setSelectedJobId(null) }}
        onSelectJob={setSelectedJobId}
        selectedJobId={selectedJobId}
      />

      {selectedJobId && (
        <JobDetail
          key={`${selectedJobId}-${detailVersion}`}
          jobId={selectedJobId}
          client={client}
          onClose={() => setSelectedJobId(null)}
          onAction={handleAction}
          onUnauthorized={onUnauthorized}
        />
      )}
    </div>
  )
}

// T5.5 — workers panel
function WorkersPanel({ workers }) {
  return (
    <section>
      <h2>Workers ({workers.length})</h2>
      {workers.length === 0 ? (
        <p>Sin workers registrados.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Hostname</th>
              <th>GPU</th>
              <th>Estado</th>
              <th>Job en curso</th>
              <th>Último heartbeat</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.id}>
                <td>{w.hostname}</td>
                <td>{w.gpu_id ?? '—'}</td>
                <td><StatusBadge status={w.status} /></td>
                <td>{w.current_job_id ?? '—'}</td>
                <td>{w.last_heartbeat ? new Date(w.last_heartbeat).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

const STATUS_OPTIONS = ['', 'pending', 'running', 'done', 'error', 'cancelled']

// T5.5 — jobs panel with status filter
function JobsPanel({ jobs, statusFilter, onStatusFilter, onSelectJob, selectedJobId }) {
  return (
    <section>
      <h2>Jobs</h2>
      <div style={{ marginBottom: 8 }}>
        {STATUS_OPTIONS.map((s) => (
          <button
            key={s || 'all'}
            onClick={() => onStatusFilter(s)}
            style={{ fontWeight: statusFilter === s ? 'bold' : 'normal', marginRight: 4 }}
          >
            {s || 'Todos'}
          </button>
        ))}
      </div>
      {jobs.length === 0 ? (
        <p>Sin jobs.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Servicio</th>
              <th>App</th>
              <th>Estado</th>
              <th>Worker</th>
              <th>Prioridad</th>
              <th>Creado</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr
                key={j.id}
                onClick={() => onSelectJob(j.id)}
                style={{
                  cursor: 'pointer',
                  background: selectedJobId === j.id ? '#e8e8e8' : undefined,
                }}
              >
                <td title={j.id}>{j.id.slice(0, 8)}…</td>
                <td>{j.service}</td>
                <td>{j.app}</td>
                <td><StatusBadge status={j.status} /></td>
                <td>{j.worker_id ?? '—'}</td>
                <td>{j.priority}</td>
                <td>{new Date(j.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

// T5.6 — job detail + cancel/retry actions
function JobDetail({ jobId, client, onClose, onAction, onUnauthorized }) {
  const [job, setJob] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [confirming, setConfirming] = useState(null) // 'cancel' | 'retry'

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(null)
    client.getJob(jobId)
      .then((j) => { if (active) { setJob(j); setLoading(false) } })
      .catch((err) => {
        if (!active) return
        if (err.message === 'unauthorized') onUnauthorized()
        else { setLoadError(err.message); setLoading(false) }
      })
    return () => { active = false }
  }, [jobId, client, onUnauthorized])

  async function handleAction(action) {
    setActionError(null)
    setConfirming(null)
    try {
      if (action === 'cancel') await client.cancelJob(jobId)
      else await client.retryJob(jobId)
      onAction()
    } catch (err) {
      if (err.message === 'unauthorized') onUnauthorized()
      else setActionError(err.message)
    }
  }

  return (
    <section style={{ borderTop: '2px solid #888', marginTop: 24, paddingTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ margin: 0 }}>Detalle del job</h2>
        <button onClick={onClose}>Cerrar ✕</button>
      </div>

      {loading && <p>Cargando…</p>}
      {loadError && <p style={{ color: 'red' }}>Error: {loadError}</p>}
      {job && (
        <>
          <dl>
            <dt>ID</dt><dd>{job.id}</dd>
            <dt>Servicio</dt><dd>{job.service}</dd>
            <dt>App</dt><dd>{job.app}</dd>
            <dt>Estado</dt><dd><StatusBadge status={job.status} /></dd>
            <dt>Worker</dt><dd>{job.worker_id ?? '—'}</dd>
            <dt>Reintentos</dt><dd>{job.retry_count} / {job.max_retries}</dd>
            <dt>Creado</dt><dd>{new Date(job.created_at).toLocaleString()}</dd>
            {job.started_at && <><dt>Iniciado</dt><dd>{new Date(job.started_at).toLocaleString()}</dd></>}
            {job.completed_at && <><dt>Completado</dt><dd>{new Date(job.completed_at).toLocaleString()}</dd></>}
            {job.error_msg && <><dt>Error</dt><dd style={{ color: '#c62828' }}>{job.error_msg}</dd></>}
          </dl>

          <h3>Payload</h3>
          <pre style={{ background: '#f5f5f5', padding: 8, overflowX: 'auto' }}>
            {JSON.stringify(job.payload, null, 2)}
          </pre>

          {actionError && <p style={{ color: 'red' }}>Error: {actionError}</p>}

          {job.status === 'pending' && (
            confirming === 'cancel' ? (
              <span>
                ¿Cancelar este job?{' '}
                <button onClick={() => handleAction('cancel')}>Confirmar</button>
                {' '}
                <button onClick={() => setConfirming(null)}>No</button>
              </span>
            ) : (
              <button onClick={() => setConfirming('cancel')}>Cancelar job</button>
            )
          )}

          {job.status === 'error' && (
            confirming === 'retry' ? (
              <span>
                ¿Reintentar este job?{' '}
                <button onClick={() => handleAction('retry')}>Confirmar</button>
                {' '}
                <button onClick={() => setConfirming(null)}>No</button>
              </span>
            ) : (
              <button onClick={() => setConfirming('retry')}>Reintentar</button>
            )
          )}
        </>
      )}
    </section>
  )
}

const STATUS_COLORS = {
  online: '#2e7d32',
  busy: '#e65100',
  offline: '#616161',
  pending: '#1565c0',
  running: '#e65100',
  done: '#2e7d32',
  error: '#c62828',
  cancelled: '#616161',
}

function StatusBadge({ status }) {
  return (
    <span style={{ color: STATUS_COLORS[status] ?? '#000', fontWeight: 'bold' }}>
      {status}
    </span>
  )
}
