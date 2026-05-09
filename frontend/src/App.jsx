import { useEffect, useRef, useState } from 'react'
import './App.css'

export default function App() {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)

  const [foto, setFoto] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [loading, setLoading] = useState(false)
  const [erro, setErro] = useState(null)

  useEffect(() => {
    iniciarCamera()
  }, [])

  async function iniciarCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
      })
      videoRef.current.srcObject = stream
    } catch {
      setErro('Não foi possível aceder à câmara.')
    }
  }

  async function tirarFoto() {
    setLoading(true)
    setErro(null)

    const video = videoRef.current
    const canvas = canvasRef.current

    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)

    setFoto(canvas.toDataURL('image/jpeg'))
    video.srcObject.getTracks().forEach(t => t.stop())

    canvas.toBlob(async (blob) => {
      try {
        const form = new FormData()
        form.append('file', blob, 'foto.jpg')

        const res = await fetch('http://127.0.0.1:8000/upload', { method: 'POST', body: form })
        if (!res.ok) throw new Error('Erro no servidor')

        setResultado(await res.json())
      } catch {
        setErro('Falha ao processar a etiqueta. Tente novamente.')
      } finally {
        setLoading(false)
      }
    }, 'image/jpeg', 1)
  }

  async function novaFoto() {
    setFoto(null)
    setResultado(null)
    setErro(null)
    await iniciarCamera()
  }

  return (
    <div className="root">
      <div className="card">

        <header className="header">
          <div className="logo-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <rect x="1" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
              <rect x="13" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
              <rect x="1" y="13" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
              <rect x="13" y="13" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
            </svg>
          </div>
          <h1 className="title">ETAPAS IMPERDÍVEIS</h1>
          <span className="badge">v1.0</span>
        </header>

        <div className="viewport">
          {!foto ? (
            <div className="camera-wrap">
              <video ref={videoRef} autoPlay playsInline className="video" />
              <div className="overlay">
                <div className="bracket tl" />
                <div className="bracket tr" />
                <div className="bracket bl" />
                <div className="bracket br" />
                {loading && <div className="scan-line" />}
              </div>
              <p className="hint">Enquadre a etiqueta na área marcada</p>
            </div>
          ) : (
            <div className="preview-wrap">
              <img src={foto} className="preview" alt="Foto capturada" />
              {loading && (
                <div className="processing-overlay">
                  <div className="spinner" />
                  <span>A analisar etiqueta…</span>
                </div>
              )}
            </div>
          )}
        </div>

        <canvas ref={canvasRef} hidden />

        {erro && <p className="erro">{erro}</p>}

        {!foto && (
          <button className="btn-primary" onClick={tirarFoto} disabled={loading}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3"/>
              <path d="M6.343 6.343a8 8 0 1 0 11.314 11.314A8 8 0 0 0 6.343 6.343"/>
              <path d="M8 2h8"/>
            </svg>
            Tirar Foto
          </button>
        )}

        {resultado && !loading && (
          <div className="resultado">
            <div className="resultado-header">
              <span className="resultado-status">
                <span className="dot" />
                Leitura concluída
              </span>
            </div>

            <div className="fields">
              <div className="field">
                <span className="field-label">Morada</span>
                <span className="field-value">{resultado.morada}</span>
              </div>
              <div className="field">
                <span className="field-label">Código Postal</span>
                <span className="field-value mono">{resultado.codigo_postal}</span>
              </div>
            </div>

            <div className="actions">
              <a href="http://127.0.0.1:8000/download-excel" target="_blank" rel="noreferrer" className="btn-download">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/></svg>
                Excel
              </a>
              <a href="http://127.0.0.1:8000/download-csv" target="_blank" rel="noreferrer" className="btn-download">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                CSV
              </a>
            </div>

            <button className="btn-secondary" onClick={novaFoto}>
              Nova Foto
            </button>
          </div>
        )}

      </div>
    </div>
  )
}