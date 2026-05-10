from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import os
import re
import uuid
import shutil
import traceback

import cv2
import pandas as pd
from paddleocr import PaddleOCR


# =========================
# FASTAPI
# =========================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# PASTAS
# =========================

os.makedirs("uploads", exist_ok=True)
os.makedirs("exports", exist_ok=True)


# =========================
# OCR
# =========================

ocr = None


def get_ocr():
    global ocr

    if ocr is None:
        print("Inicializando PaddleOCR...", flush=True)

        ocr = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            show_log=False
        )

        print("PaddleOCR inicializado", flush=True)

    return ocr


# =========================
# ROTAS DE TESTE
# =========================

@app.get("/")
async def home():
    return {
        "status": "online",
        "message": "API OCR funcionando"
    }


@app.get("/health")
async def health():
    return {
        "status": "ok"
    }


# =========================
# LIMPAR TEXTO
# =========================

def limpar_texto(texto):
    texto = texto.replace("\r", "\n")
    texto = re.sub(r"\n+", "\n", texto)
    return texto.strip()


# =========================
# EXTRAIR CODIGO POSTAL
# =========================

def extrair_codigo(texto):
    match = re.search(r"\d{4}-\d{3}", texto)

    if match:
        return match.group()

    return ""


# =========================
# EXTRAIR MORADA
# =========================

def extrair_morada(texto):
    linhas = texto.split("\n")

    palavras_morada = [
        "RUA",
        "AVENIDA",
        "AV.",
        "ALAMEDA",
        "TRAVESSA",
        "LARGO",
        "PRAÇA",
        "PRACA",
        "ESTRADA",
        "CAMINHO",
        "URBANIZAÇÃO",
        "URBANIZACAO"
    ]

    for linha in linhas:
        linha_limpa = linha.strip()
        linha_upper = linha_limpa.upper()

        for palavra in palavras_morada:
            if palavra in linha_upper:
                return linha_limpa

    return ""


# =========================
# EXTRAIR TEXTO DO RESULTADO OCR
# =========================

def extrair_texto_ocr(resultado):
    texto = ""

    if not resultado:
        return texto

    for bloco in resultado:
        if not bloco:
            continue

        for linha in bloco:
            try:
                txt = linha[1][0]
                texto += txt + "\n"
            except Exception:
                continue

    return texto


# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    caminho = None
    temp_path = None

    try:
        print("\n========== RECEBEU UPLOAD ==========", flush=True)
        print(f"Arquivo: {file.filename}", flush=True)
        print(f"Tipo: {file.content_type}", flush=True)

        if not file.content_type or not file.content_type.startswith("image/"):
            return JSONResponse(
                status_code=400,
                content={
                    "erro": "O arquivo enviado não é uma imagem."
                }
            )

        nome = f"{uuid.uuid4()}.jpg"
        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        print(f"Imagem salva em: {caminho}", flush=True)

        img = cv2.imread(caminho)

        if img is None:
            print("Erro: cv2 não conseguiu abrir a imagem", flush=True)

            return JSONResponse(
                status_code=400,
                content={
                    "erro": "Erro ao abrir imagem."
                }
            )

        print("Imagem aberta com sucesso", flush=True)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        gray = cv2.resize(
            gray,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC
        )

        gray = cv2.threshold(
            gray,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]

        temp_path = f"uploads/temp_{uuid.uuid4()}.jpg"

        cv2.imwrite(temp_path, gray)

        print(f"Imagem processada salva em: {temp_path}", flush=True)

        print("Iniciando OCR...", flush=True)

        engine = get_ocr()

        resultado = engine.ocr(
            temp_path,
            cls=True
        )

        print("OCR finalizado", flush=True)

        texto = extrair_texto_ocr(resultado)
        texto = limpar_texto(texto)

        print("\n========== TEXTO OCR ==========", flush=True)
        print(texto, flush=True)

        codigo = extrair_codigo(texto)
        morada = extrair_morada(texto)

        print("\n========== DADOS EXTRAÍDOS ==========", flush=True)
        print(f"Morada: {morada}", flush=True)
        print(f"Código Postal: {codigo}", flush=True)

        dados = pd.DataFrame([{
            "Morada": morada,
            "Código Postal": codigo,
            "Texto OCR": texto
        }])

        arquivo_excel = "exports/resultado.xlsx"
        arquivo_csv = "exports/resultado.csv"

        if os.path.exists(arquivo_excel):
            antigo = pd.read_excel(arquivo_excel)

            final = pd.concat(
                [antigo, dados],
                ignore_index=True
            )
        else:
            final = dados

        final.to_excel(
            arquivo_excel,
            index=False
        )

        final.to_csv(
            arquivo_csv,
            index=False
        )

        print("Arquivos Excel/CSV salvos com sucesso", flush=True)

        return {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": codigo if codigo else "Não encontrado",
            "texto_ocr": texto if texto else "Nenhum texto encontrado"
        }

    except Exception as e:
        print("\n========== ERRO ==========", flush=True)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "erro": str(e)
            }
        )

    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


# =========================
# DOWNLOAD EXCEL
# =========================

@app.get("/download-excel")
async def download_excel():

    arquivo_excel = "exports/resultado.xlsx"

    if not os.path.exists(arquivo_excel):
        pd.DataFrame(columns=[
            "Morada",
            "Código Postal",
            "Texto OCR"
        ]).to_excel(
            arquivo_excel,
            index=False
        )

    return FileResponse(
        path=arquivo_excel,
        filename="resultado.xlsx"
    )


# =========================
# DOWNLOAD CSV
# =========================

@app.get("/download-csv")
async def download_csv():

    arquivo_csv = "exports/resultado.csv"

    if not os.path.exists(arquivo_csv):
        pd.DataFrame(columns=[
            "Morada",
            "Código Postal",
            "Texto OCR"
        ]).to_csv(
            arquivo_csv,
            index=False
        )

    return FileResponse(
        path=arquivo_csv,
        filename="resultado.csv"
    )