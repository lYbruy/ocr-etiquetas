import { useEffect, useRef, useState } from 'react'
import './App.css'

const API = 'https://ocr-etiquetas-production.up.railway.app'

export default function App() {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const detectorCanvasRef = useRef(null)
  const autoTimerRef = useRef(null)
  const stableCountRef = useRef(0)
  const capturandoRef = useRef(false)

  const [foto, setFoto] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [loading, setLoading] = useState(false)
  const [erro, setErro] = useState(null)
  const [autoStatus, setAutoStatus] = useState('Aponte para a etiqueta')
  const [adicionado, setAdicionado] = useState(false)
  const [totalLote, setTotalLote] = useState(0)

  const [moradaEdit, setMoradaEdit] = useState('')
  const [codigoEdit, setCodigoEdit] = useState('')
  const [cidadeEdit, setCidadeEdit] = useState('')

  useEffect(() => {
    iniciarSistema()

    return () => {
      pararAutoDetector()
      pararCamera()
    }
  }, [])

  async function iniciarSistema() {
    await limparLoteInicial()
    await iniciarCamera()
  }

  async function limparLoteInicial() {
    try {
      await fetch(`${API}/limpar-lote`, {
        method: 'POST',
      })

      setTotalLote(0)
    } catch (e) {
      console.error(e)
    }
  }

  async function atualizarResumoLote() {
    try {
      const response = await fetch(`${API}/resumo-lote`)
      const data = await response.json()

      setTotalLote(data?.total || 0)
    } catch (e) {
      console.error(e)
    }
  }

  async function iniciarCamera() {
    try {
      setErro(null)
      setAutoStatus('A abrir câmara...')

      pararAutoDetector()
      pararCamera()

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'environment',
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      })

      if (videoRef.current) {
        videoRef.current.srcObject = stream

        await videoRef.current.play().catch(() => {})

        videoRef.current.onloadedmetadata = () => {
          setAutoStatus('Aponte para a etiqueta')
          iniciarAutoDetector()
        }

        setTimeout(() => {
          iniciarAutoDetector()
        }, 800)
      }
    } catch (e) {
      console.error(e)
      setErro('Não foi possível aceder à câmara.')
    }
  }

  function pararCamera() {
    try {
      const video = videoRef.current

      if (video && video.srcObject) {
        video.srcObject
          .getTracks()
          .forEach(track => track.stop())

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
    }, 700)
  }

  function pararAutoDetector() {
    if (autoTimerRef.current) {
      clearInterval(autoTimerRef.current)
      autoTimerRef.current = null
    }

    stableCountRef.current = 0
  }

  function detectarEtiquetaECapturar() {
    const video = videoRef.current
    const canvas = detectorCanvasRef.current

    if (!video || !canvas || foto || loading || capturandoRef.current) {
      return
    }

    if (!video.videoWidth || !video.videoHeight) {
      return
    }

    const width = 260
    const height = 170

    canvas.width = width
    canvas.height = height

    const ctx = canvas.getContext('2d', { willReadFrequently: true })

    ctx.drawImage(video, 0, 0, width, height)

    const image = ctx.getImageData(0, 0, width, height)
    const data = image.data

    let bright = 0
    let dark = 0
    let edges = 0
    let total = 0

    const startX = Math.floor(width * 0.08)
    const endX = Math.floor(width * 0.92)
    const startY = Math.floor(height * 0.12)
    const endY = Math.floor(height * 0.88)

    let lastLum = null

    for (let y = startY; y < endY; y += 2) {
      for (let x = startX; x < endX; x += 2) {
        const i = (y * width + x) * 4

        const r = data[i]
        const g = data[i + 1]
        const b = data[i + 2]

        const lum = (r + g + b) / 3

        if (lum > 165) bright++
        if (lum < 95) dark++

        if (lastLum !== null && Math.abs(lum - lastLum) > 45) {
          edges++
        }

        lastLum = lum
        total++
      }
    }

    const brightRatio = bright / total
    const darkRatio = dark / total
    const edgeRatio = edges / total

    const pareceEtiqueta =
      brightRatio > 0.22 &&
      brightRatio < 0.92 &&
      darkRatio > 0.01 &&
      edgeRatio > 0.025

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
    setLoading(true)
    setErro(null)
    setResultado(null)
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

      ctx.drawImage(
        video,
        0,
        0,
        canvas.width,
        canvas.height
      )

      const imageData = canvas.toDataURL(
        'image/jpeg',
        0.92
      )

      setFoto(imageData)
      setAutoStatus(auto ? 'Foto capturada automaticamente' : 'Foto capturada')

      pararAutoDetector()
      pararCamera()

      canvas.toBlob(async (blob) => {
        if (!blob) {
          setErro('Erro ao gerar imagem.')
          setLoading(false)
          capturandoRef.current = false
          return
        }

        const form = new FormData()

        form.append(
          'file',
          blob,
          'foto.jpg'
        )

        try {
          console.log('Enviando imagem para API...')

          const response = await fetch(
            `${API}/upload`,
            {
              method: 'POST',
              body: form,
            }
          )

          const data = await response.json().catch(() => null)

          console.log('STATUS:', response.status)
          console.log('RESPOSTA:', data)

          if (!response.ok) {
            throw new Error(data?.erro || `Erro HTTP ${response.status}`)
          }

          if (data?.erro) {
            throw new Error(data.erro)
          }

          setResultado(data)

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

          setCidadeEdit(
            data?.cidade && data.cidade !== 'Não encontrada'
              ? data.cidade
              : ''
          )
        } catch (e) {
          console.error(e)

          setErro(
            e.message || 'Falha ao processar a etiqueta.'
          )
        } finally {
          setLoading(false)
          capturandoRef.current = false
        }
      }, 'image/jpeg', 0.92)

    } catch (e) {
      console.error(e)

      setErro(
        e.message || 'Erro ao tirar foto.'
      )

      setLoading(false)
      capturandoRef.current = false
    }
  }

  async function confirmarEtiqueta() {
    try {
      setErro(null)
      setLoading(true)

      const response = await fetch(`${API}/confirmar`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          upload_id: resultado?.upload_id,
          morada: moradaEdit,
          codigo_postal: codigoEdit,
          cidade: cidadeEdit,
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

      setTimeout(() => {
        proximaEtiqueta()
      }, 800)
    } catch (e) {
      console.error(e)
      setErro(e.message || 'Erro ao confirmar etiqueta.')
    } finally {
      setLoading(false)
    }
  }

  async function proximaEtiqueta() {
    setFoto(null)
    setResultado(null)
    setErro(null)
    setLoading(false)
    setAdicionado(false)
    setMoradaEdit('')
    setCodigoEdit('')
    setCidadeEdit('')
    capturandoRef.current = false
    stableCountRef.current = 0

    await iniciarCamera()
  }

  async function limparTudo() {
    try {
      setErro(null)
      setLoading(true)

      await fetch(`${API}/limpar-lote`, {
        method: 'POST',
      })

      setTotalLote(0)
      await proximaEtiqueta()
    } catch (e) {
      console.error(e)
      setErro('Erro ao limpar lote.')
    } finally {
      setLoading(false)
    }
  }

  const resultados = resultado?.todos_resultados || []

  return (
    <div className="root">

      <div className="card">

        <header className="header">

          <div className="logo-mark">

            <svg
              width="20"
              height="20"
              viewBox="0 0 20 20"
              fill="none"
            >
              <rect
                x="1"
                y="1"
                width="6"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.5"
              />

              <rect
                x="13"
                y="1"
                width="6"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.5"
              />

              <rect
                x="1"
                y="13"
                width="6"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.5"
              />

              <rect
                x="13"
                y="13"
                width="6"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.5"
              />
            </svg>

          </div>

          <h1 className="title">
            ETAPAS IMPERDÍVEIS
          </h1>

          <span className="badge">
            v1.2
          </span>

        </header>

        <div className="lote-box">
          <span>
            Etiquetas no lote
          </span>

          <strong>
            {totalLote}
          </strong>
        </div>

        <div className="viewport">

          {!foto ? (

            <div className="camera-wrap">

              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="video"
              />

              <div className="overlay">

                <div className="bracket tl" />
                <div className="bracket tr" />
                <div className="bracket bl" />
                <div className="bracket br" />
                <div className="scan-line" />

              </div>

              <p className="hint">
                {autoStatus}
              </p>

            </div>

          ) : (

            <div className="preview-wrap">

              <img
                src={foto}
                className="preview"
                alt="Foto"
              />

              {loading && (

                <div className="processing-overlay">

                  <div className="spinner" />

                  <span>
                    A analisar etiqueta...
                  </span>

                </div>

              )}

            </div>

          )}

        </div>

        <canvas
          ref={canvasRef}
          hidden
        />

        <canvas
          ref={detectorCanvasRef}
          hidden
        />

        {erro && (
          <p className="erro">
            {erro}
          </p>
        )}

        {!foto && (

          <button
            className="btn-primary"
            onClick={() => tirarFoto(false)}
            disabled={loading}
          >
            Tirar Foto Manualmente
          </button>

        )}

        {resultado && !loading && (

          <div className="resultado">

            <div className="resultado-header">

              <span className="resultado-status">

                <span className="dot" />

                Confirmar etiqueta

              </span>

            </div>

            <div className="edit-fields">

              <label className="edit-label">
                Morada
                <input
                  className="edit-input"
                  value={moradaEdit}
                  onChange={(e) => setMoradaEdit(e.target.value)}
                  placeholder="Ex: AVENIDA EUROPA Nº292"
                />
              </label>

              <label className="edit-label">
                Código Postal
                <input
                  className="edit-input mono"
                  value={codigoEdit}
                  onChange={(e) => setCodigoEdit(e.target.value)}
                  placeholder="Ex: 3800-974"
                />
              </label>

              <label className="edit-label">
                Localidade
                <input
                  className="edit-input"
                  value={cidadeEdit}
                  onChange={(e) => setCidadeEdit(e.target.value)}
                  placeholder="Ex: AVEIRO"
                />
              </label>

            </div>

            {resultados.length > 0 && (

              <div className="candidatos">

                <span className="field-label">
                  Sugestões encontradas
                </span>

                {resultados.map((item, index) => (

                  <button
                    type="button"
                    className="candidate"
                    key={`${item.codigo_postal}-${index}`}
                    onClick={() => {
                      setMoradaEdit(item.morada !== 'Não encontrada' ? item.morada : '')
                      setCodigoEdit(item.codigo_postal || '')
                      setCidadeEdit(item.cidade !== 'Não encontrada' ? item.cidade : '')
                    }}
                  >
                    <span>
                      {item.morada}
                    </span>

                    <strong>
                      {item.codigo_postal}
                      {item.cidade && item.cidade !== 'Não encontrada' ? ` ${item.cidade}` : ''}
                    </strong>
                  </button>

                ))}

              </div>

            )}

            <button
              className="btn-primary"
              onClick={confirmarEtiqueta}
              disabled={loading || adicionado}
            >
              {adicionado ? 'Adicionado ao lote' : 'Confirmar e adicionar ao lote'}
            </button>

            <button
              className="btn-secondary"
              onClick={proximaEtiqueta}
              disabled={loading}
            >
              Ignorar e ler próxima
            </button>

          </div>

        )}

        {totalLote > 0 && (

          <div className="actions">

            <a
              href={`${API}/download-excel`}
              target="_blank"
              rel="noreferrer"
              className="btn-download"
            >
              Exportar Excel
            </a>

            <a
              href={`${API}/download-csv`}
              target="_blank"
              rel="noreferrer"
              className="btn-download"
            >
              Exportar CSV
            </a>

          </div>

        )}

        {totalLote > 0 && (

          <button
            className="btn-secondary"
            onClick={limparTudo}
            disabled={loading}
          >
            Limpar lote
          </button>

        )}

      </div>

    </div>
  )
}