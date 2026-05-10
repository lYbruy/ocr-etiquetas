import { useEffect, useRef, useState } from 'react'
import './App.css'

const API = 'https://ocr-etiquetas-production.up.railway.app'

export default function App() {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)

  const [foto, setFoto] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [loading, setLoading] = useState(false)
  const [erro, setErro] = useState(null)

  useEffect(() => {
    iniciarCamera()

    return () => {
      pararCamera()
    }
  }, [])

  async function iniciarCamera() {
    try {
      setErro(null)

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'environment',
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      })

      if (videoRef.current) {
        videoRef.current.srcObject = stream
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

  async function tirarFoto() {
    setLoading(true)
    setErro(null)
    setResultado(null)

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
        1
      )

      setFoto(imageData)

      pararCamera()

      canvas.toBlob(async (blob) => {
        if (!blob) {
          setErro('Erro ao gerar imagem.')
          setLoading(false)
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
        } catch (e) {
          console.error(e)

          setErro(
            e.message || 'Falha ao processar a etiqueta.'
          )
        } finally {
          setLoading(false)
        }
      }, 'image/jpeg', 1)

    } catch (e) {
      console.error(e)

      setErro(
        e.message || 'Erro ao tirar foto.'
      )

      setLoading(false)
    }
  }

  async function novaFoto() {
    setFoto(null)
    setResultado(null)
    setErro(null)
    setLoading(false)

    await iniciarCamera()
  }

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
            v1.0
          </span>

        </header>

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

              </div>

              <p className="hint">
                Enquadre a etiqueta na área marcada
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

        {erro && (
          <p className="erro">
            {erro}
          </p>
        )}

        {!foto && (

          <button
            className="btn-primary"
            onClick={tirarFoto}
            disabled={loading}
          >

            Tirar Foto

          </button>

        )}

        {foto && erro && !loading && (

          <button
            className="btn-secondary"
            onClick={novaFoto}
          >
            Nova Foto
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

                <span className="field-label">
                  Morada
                </span>

                <span className="field-value">
                  {resultado.morada || 'Não encontrada'}
                </span>

              </div>

              <div className="field">

                <span className="field-label">
                  Código Postal
                </span>

                <span className="field-value mono">
                  {resultado.codigo_postal || 'Não encontrado'}
                </span>

              </div>

            </div>

            <div className="actions">

              <a
                href={`${API}/download-excel`}
                target="_blank"
                rel="noreferrer"
                className="btn-download"
              >
                Excel
              </a>

              <a
                href={`${API}/download-csv`}
                target="_blank"
                rel="noreferrer"
                className="btn-download"
              >
                CSV
              </a>

            </div>

            <button
              className="btn-secondary"
              onClick={novaFoto}
            >
              Nova Foto
            </button>

          </div>

        )}

      </div>

    </div>
  )
}