import { useEffect, useRef, useState } from 'react'
import './App.css'

const API = 'https://ocr-etiquetas-production.up.railway.app'

export default function App() {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const detectorCanvasRef = useRef(null)
  const autoTimerRef = useRef(null)

  const fotoRef = useRef(false)
  const loadingRef = useRef(false)
  const resultadoRef = useRef(false)
  const capturandoRef = useRef(false)
  const stableCountRef = useRef(0)
  const cooldownRef = useRef(false)

  const [foto, setFoto] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [loading, setLoading] = useState(false)
  const [erro, setErro] = useState(null)

  const [autoStatus, setAutoStatus] = useState('A iniciar sistema...')
  const [adicionado, setAdicionado] = useState(false)
  const [totalLote, setTotalLote] = useState(0)

  const [moradaEdit, setMoradaEdit] = useState('')
  const [codigoEdit, setCodigoEdit] = useState('')

  useEffect(() => {
    iniciarSistema()

    return () => {
      pararAutoDetector()
      pararCamera()
    }
  }, [])

  function atualizarLoading(valor) {
    loadingRef.current = valor
    setLoading(valor)
  }

  function atualizarFoto(valor) {
    fotoRef.current = Boolean(valor)
    setFoto(valor)
  }

  function atualizarResultado(valor) {
    resultadoRef.current = Boolean(valor)
    setResultado(valor)
  }

  async function iniciarSistema() {
    await atualizarResumoLote()
    await iniciarCamera()
  }

  async function atualizarResumoLote() {
    try {
      const response = await fetch(`${API}/resumo-lote`)
      const data = await response.json().catch(() => null)

      if (data?.total !== undefined) {
        setTotalLote(data.total)
      }
    } catch (e) {
      console.error(e)
    }
  }

  async function iniciarCamera() {
    try {
      setErro(null)
      setAutoStatus('A abrir câmara...')

      pararAutoDetector()

      if (videoRef.current?.srcObject) {
        setAutoStatus('Aponte para a etiqueta')
        iniciarAutoDetector()
        return
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'environment',
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      })

      if (videoRef.current) {
        videoRef.current.srcObject = stream

        videoRef.current.onloadedmetadata = async () => {
          try {
            await videoRef.current.play()
          } catch (e) {
            console.error(e)
          }

          setAutoStatus('Aponte para a etiqueta')
          iniciarAutoDetector()
        }

        setTimeout(() => {
          iniciarAutoDetector()
        }, 900)
      }
    } catch (e) {
      console.error(e)
      setErro('Não foi possível aceder à câmara.')
      setAutoStatus('Erro ao abrir câmara')
    }
  }

  function pararCamera() {
    try {
      const video = videoRef.current

      if (video && video.srcObject) {
        video.srcObject.getTracks().forEach(track => track.stop())
        video.srcObject = null
      }
    } catch (e) {
      console.error(e)
    }
  }

  function iniciarAutoDetector() {
    pararAutoDetector()
    stableCountRef.current = 0

    autoTimerRef.current = setInterval(() => {
      detectarEtiquetaECapturar()
    }, 750)
  }

  function pararAutoDetector() {
    if (autoTimerRef.current) {
      clearInterval(autoTimerRef.current)
      autoTimerRef.current = null
    }

    stableCountRef.current = 0
  }

  function ativarCooldown(ms = 1400) {
    cooldownRef.current = true

    setTimeout(() => {
      cooldownRef.current = false
    }, ms)
  }

  function detectarEtiquetaECapturar() {
    const video = videoRef.current
    const canvas = detectorCanvasRef.current

    if (!video || !canvas) return

    if (
      fotoRef.current ||
      loadingRef.current ||
      resultadoRef.current ||
      capturandoRef.current ||
      cooldownRef.current
    ) {
      return
    }

    if (!video.videoWidth || !video.videoHeight) return

    const width = 280
    const height = 180

    canvas.width = width
    canvas.height = height

    const ctx = canvas.getContext('2d', {
      willReadFrequently: true,
    })

    ctx.drawImage(video, 0, 0, width, height)

    const image = ctx.getImageData(0, 0, width, height)
    const data = image.data

    let bright = 0
    let dark = 0
    let edges = 0
    let total = 0

    const startX = Math.floor(width * 0.06)
    const endX = Math.floor(width * 0.94)
    const startY = Math.floor(height * 0.10)
    const endY = Math.floor(height * 0.90)

    for (let y = startY; y < endY; y += 3) {
      for (let x = startX; x < endX; x += 3) {
        const i = (y * width + x) * 4

        const r = data[i]
        const g = data[i + 1]
        const b = data[i + 2]

        const lum = (r + g + b) / 3

        if (lum > 155) bright++
        if (lum < 95) dark++

        if (x + 3 < endX) {
          const j = (y * width + (x + 3)) * 4

          const r2 = data[j]
          const g2 = data[j + 1]
          const b2 = data[j + 2]

          const lum2 = (r2 + g2 + b2) / 3

          if (Math.abs(lum - lum2) > 42) {
            edges++
          }
        }

        total++
      }
    }

    const brightRatio = bright / total
    const darkRatio = dark / total
    const edgeRatio = edges / total

    const pareceEtiqueta =
      brightRatio > 0.18 &&
      brightRatio < 0.94 &&
      darkRatio > 0.006 &&
      edgeRatio > 0.012

    if (pareceEtiqueta) {
      stableCountRef.current += 1
      setAutoStatus(`Etiqueta detectada ${stableCountRef.current}/2`)

      if (stableCountRef.current >= 2) {
        capturandoRef.current = true
        pararAutoDetector()
        tirarFoto(true)
      }
    } else {
      stableCountRef.current = 0
      setAutoStatus('Aponte para a etiqueta')
    }
  }

  async function tirarFoto(auto = false) {
    if (loadingRef.current) return

    atualizarLoading(true)
    setErro(null)
    atualizarResultado(null)
    setAdicionado(false)

    try {
      const video = videoRef.current
      const canvas = canvasRef.current

      if (!video || !canvas) {
        throw new Error('Câmara não encontrada.')
      }

      if (!video.videoWidth || !video.videoHeight) {
        throw new Error('A câmara ainda não carregou. Tente novamente.')
      }

      canvas.width = video.videoWidth
      canvas.height = video.videoHeight

      const ctx = canvas.getContext('2d')
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

      const imageData = canvas.toDataURL('image/jpeg', 0.9)

      atualizarFoto(imageData)
      setAutoStatus(auto ? 'Foto capturada automaticamente' : 'Foto capturada')

      canvas.toBlob(async (blob) => {
        if (!blob) {
          setErro('Erro ao gerar imagem.')
          atualizarLoading(false)
          capturandoRef.current = false
          iniciarAutoDetector()
          return
        }

        const form = new FormData()
        form.append('file', blob, 'foto.jpg')

        try {
          const response = await fetch(`${API}/upload`, {
            method: 'POST',
            body: form,
          })

          const data = await response.json().catch(() => null)

          console.log('STATUS:', response.status)
          console.log('RESPOSTA:', data)

          if (!response.ok) {
            throw new Error(data?.erro || `Erro HTTP ${response.status}`)
          }

          if (data?.erro) {
            throw new Error(data.erro)
          }

          atualizarResultado(data)

          setMoradaEdit(
            data?.morada && data.morada !== 'Não encontrada'
              ? data.morada
              : ''
          )

          setCodigoEdit(
            data?.codigo_postal && data.codigo_postal !== 'Não encontrado'
              ? data.codigo_postal
              : ''
          )

          setAutoStatus('Confirme os dados encontrados')
          await atualizarResumoLote()
        } catch (e) {
          console.error(e)
          setErro(e.message || 'Falha ao processar a etiqueta.')
          atualizarResultado(null)
        } finally {
          atualizarLoading(false)
          capturandoRef.current = false
        }
      }, 'image/jpeg', 0.9)
    } catch (e) {
      console.error(e)
      setErro(e.message || 'Erro ao tirar foto.')
      atualizarLoading(false)
      capturandoRef.current = false
      iniciarAutoDetector()
    }
  }

  async function tirarFotoManual() {
    capturandoRef.current = true
    pararAutoDetector()
    await tirarFoto(false)
  }

  async function confirmarEtiqueta() {
    try {
      setErro(null)
      atualizarLoading(true)

      const response = await fetch(`${API}/confirmar`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          upload_id: resultado?.upload_id,
          morada: moradaEdit,
          codigo_postal: codigoEdit,
          cidade: '',
          texto_ocr: resultado?.texto_ocr || '',
        }),
      })

      const data = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(data?.erro || `Erro HTTP ${response.status}`)
      }

      if (data?.erro) {
        throw new Error(data.erro)
      }

      setAdicionado(true)
      setTotalLote(data?.total_lote || 0)
      await atualizarResumoLote()

      setTimeout(() => {
        proximaEtiqueta()
      }, 700)
    } catch (e) {
      console.error(e)
      setErro(e.message || 'Erro ao confirmar etiqueta.')
    } finally {
      atualizarLoading(false)
    }
  }

  async function proximaEtiqueta() {
    atualizarFoto(null)
    atualizarResultado(null)

    setErro(null)
    atualizarLoading(false)
    setAdicionado(false)

    setMoradaEdit('')
    setCodigoEdit('')

    capturandoRef.current = false
    stableCountRef.current = 0

    ativarCooldown(1400)
    setAutoStatus('Aponte para a próxima etiqueta')

    if (!videoRef.current?.srcObject) {
      await iniciarCamera()
    } else {
      iniciarAutoDetector()
    }

    await atualizarResumoLote()
  }

  async function limparTudo() {
    try {
      setErro(null)
      atualizarLoading(true)

      const response = await fetch(`${API}/limpar-lote`, {
        method: 'POST',
      })

      const data = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(data?.erro || `Erro HTTP ${response.status}`)
      }

      setTotalLote(0)
      await proximaEtiqueta()
    } catch (e) {
      console.error(e)
      setErro(e.message || 'Erro ao limpar lote.')
    } finally {
      atualizarLoading(false)
    }
  }

  async function baixarArquivo(tipo) {
    try {
      if (totalLote === 0 || loading) return

      setErro(null)
      atualizarLoading(true)

      const endpoint = tipo === 'excel' ? 'download-excel' : 'download-csv'
      const filename = tipo === 'excel' ? 'resultado.xlsx' : 'resultado.csv'

      const response = await fetch(`${API}/${endpoint}`)

      if (!response.ok) {
        throw new Error(`Erro ao exportar ${tipo === 'excel' ? 'Excel' : 'CSV'}.`)
      }

      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)

      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()

      window.URL.revokeObjectURL(url)

      await atualizarResumoLote()
    } catch (e) {
      console.error(e)
      setErro(e.message || 'Erro ao exportar ficheiro.')
    } finally {
      atualizarLoading(false)
    }
  }

  const resultados = resultado?.todos_resultados || []

  return (
    <div className="app-shell">
      <div className="app-background">
        <span className="blur blur-one" />
        <span className="blur blur-two" />
        <span className="grid-bg" />
      </div>

      <main className="app-container">
        <section className="hero-panel">
          <div className="brand-row">
            <div className="brand-icon">
              <span>EI</span>
            </div>

            <div>
              <p className="eyebrow">Sistema OCR profissional</p>
              <h1>Etapas Imperdíveis</h1>
            </div>

            <span className="version-pill">v2.5</span>
          </div>

          <div className="hero-content">
            <h2>Leitura inteligente de etiquetas</h2>
            <p>
              Aponte a câmara para uma etiqueta, confirme os dados encontrados
              e exporte o lote completo em Excel ou CSV.
            </p>
          </div>

          <div className="stats-grid">
            <div className="stat-card active">
              <span>Etiquetas no lote</span>
              <strong>{totalLote}</strong>
            </div>

            <div className="stat-card">
              <span>Estado</span>
              <strong>{loading ? 'A processar' : 'Pronto'}</strong>
            </div>
          </div>
        </section>

        <section className="scanner-panel">
          <div className="panel-top">
            <div>
              <p className="section-label">Scanner</p>
              <h3>Captura da etiqueta</h3>
            </div>

            <div className={`status-chip ${loading ? 'loading' : ''}`}>
              <span />
              {loading ? 'A analisar' : 'Online'}
            </div>
          </div>

          <div className="camera-frame">
            <div className={`camera-wrap ${foto ? 'camera-hidden' : ''}`}>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="video"
              />

              <div className="camera-overlay">
                <div className="focus-box">
                  <span className="corner top-left" />
                  <span className="corner top-right" />
                  <span className="corner bottom-left" />
                  <span className="corner bottom-right" />
                  <span className="scan-line" />
                </div>

                <div className="camera-bottom">
                  <p>{autoStatus}</p>
                </div>
              </div>
            </div>

            {foto && (
              <div className="preview-wrap">
                <img
                  src={foto}
                  className="preview"
                  alt="Foto capturada"
                />

                {loading && (
                  <div className="processing-overlay">
                    <div className="loader-ring" />
                    <h4>A analisar etiqueta</h4>
                    <p>Estamos a extrair a morada e o código postal.</p>
                  </div>
                )}
              </div>
            )}
          </div>

          <canvas ref={canvasRef} hidden />
          <canvas ref={detectorCanvasRef} hidden />

          {erro && (
            <div className="alert-error">
              <strong>Erro</strong>
              <p>{erro}</p>
            </div>
          )}

          {!foto && (
            <button
              className="btn btn-primary"
              onClick={tirarFotoManual}
              disabled={loading}
            >
              <span className="btn-icon">⌁</span>
              Tirar foto manualmente
            </button>
          )}

          {resultado && !loading && (
            <div className="result-card">
              <div className="result-header">
                <div>
                  <p className="section-label">Resultado encontrado</p>
                  <h3>
                    {resultado?.geo_validada
                      ? 'Morada validada online'
                      : 'Confirme os dados'}
                  </h3>
                </div>

                <span className={`validation-badge ${resultado?.geo_validada ? 'success' : 'warning'}`}>
                  {resultado?.geo_validada ? 'Validada' : 'Revisar'}
                </span>
              </div>

              <div className="form-grid">
                <label className="form-field">
                  <span>Morada</span>
                  <input
                    value={moradaEdit}
                    onChange={(e) => setMoradaEdit(e.target.value)}
                    placeholder="Morada encontrada na etiqueta"
                  />
                </label>

                <label className="form-field">
                  <span>Código postal</span>
                  <input
                    className="mono"
                    value={codigoEdit}
                    onChange={(e) => setCodigoEdit(e.target.value)}
                    placeholder="0000-000"
                  />
                </label>
              </div>

              {resultados.length > 0 && (
                <div className="suggestions-box">
                  <div className="suggestions-title">
                    <span>Sugestões encontradas</span>
                    <small>{resultados.length} opção/opções</small>
                  </div>

                  <div className="suggestions-list">
                    {resultados.map((item, index) => (
                      <button
                        type="button"
                        className="suggestion-item"
                        key={`${item.codigo_postal}-${index}`}
                        onClick={() => {
                          setMoradaEdit(
                            item.morada !== 'Não encontrada'
                              ? item.morada
                              : ''
                          )

                          setCodigoEdit(item.codigo_postal || '')
                        }}
                      >
                        <span>{item.morada}</span>
                        <strong>{item.codigo_postal}</strong>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="result-actions">
                <button
                  className="btn btn-primary"
                  onClick={confirmarEtiqueta}
                  disabled={loading || adicionado}
                >
                  {adicionado ? 'Adicionado ao lote' : 'Confirmar e adicionar'}
                </button>

                <button
                  className="btn btn-secondary"
                  onClick={proximaEtiqueta}
                  disabled={loading}
                >
                  Ignorar e próxima
                </button>
              </div>
            </div>
          )}

          <div className="export-panel">
            <div>
              <p className="section-label">Exportação</p>
              <h3>Ficheiros do lote</h3>
            </div>

            <div className="export-actions">
              <button
                type="button"
                onClick={() => baixarArquivo('excel')}
                className="btn btn-download"
                disabled={loading || totalLote === 0}
              >
                Excel
              </button>

              <button
                type="button"
                onClick={() => baixarArquivo('csv')}
                className="btn btn-download"
                disabled={loading || totalLote === 0}
              >
                CSV
              </button>
            </div>
          </div>

          <button
            className="btn btn-danger"
            onClick={limparTudo}
            disabled={loading}
          >
            Limpar lote
          </button>
        </section>
      </main>
    </div>
  )
}