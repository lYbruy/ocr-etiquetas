from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import shutil
import cv2
import pandas as pd
import os
import uuid
import re

from paddleocr import PaddleOCR

# =========================
# OCR
# =========================

ocr = PaddleOCR(
    use_angle_cls=True,
    lang='en'
)

# =========================
# FASTAPI
# =========================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# PASTAS
# =========================

os.makedirs("uploads", exist_ok=True)
os.makedirs("exports", exist_ok=True)

# =========================
# LIMPAR TEXTO
# =========================

def limpar_texto(texto):

    texto = texto.replace("\n", " ")

    return texto.strip()

# =========================
# EXTRAIR CODIGO POSTAL
# =========================

def extrair_codigo(texto):

    match = re.search(
        r'\d{4}-\d{3}',
        texto
    )

    if match:
        return match.group()

    return ""

# =========================
# EXTRAIR MORADA
# =========================

def extrair_morada(texto):

    linhas = texto.split("\n")

    for linha in linhas:

        linha_upper = linha.upper()

        if (
            "RUA" in linha_upper
            or "AVENIDA" in linha_upper
            or "ALAMEDA" in linha_upper
        ):

            return linha

    return ""

# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    try:

        # salvar foto

        nome = f"{uuid.uuid4()}.jpg"

        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:

            shutil.copyfileobj(
                file.file,
                buffer
            )

        # abrir imagem

        img = cv2.imread(caminho)

        if img is None:

            return {
                "erro": "Erro ao abrir imagem"
            }

        # melhorar imagem

        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

        gray = cv2.resize(
            gray,
            None,
            fx=2,
            fy=2
        )

        gray = cv2.threshold(
            gray,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]

        temp_path = "temp.jpg"

        cv2.imwrite(
            temp_path,
            gray
        )

        # OCR

        resultado = ocr.ocr(
            temp_path,
            cls=True
        )

        texto = ""

        for bloco in resultado:

            for linha in bloco:

                txt = linha[1][0]

                texto += txt + "\n"

        texto = limpar_texto(texto)

        print("\n========== OCR ==========\n")
        print(texto)

        # extrair dados

        codigo = extrair_codigo(
            texto
        )

        morada = extrair_morada(
            texto
        )

        # dataframe

        dados = pd.DataFrame([{
            "Morada": morada,
            "Código Postal": codigo
        }])

        arquivo_excel = "exports/resultado.xlsx"

        if os.path.exists(
            arquivo_excel
        ):

            antigo = pd.read_excel(
                arquivo_excel
            )

            final = pd.concat(
                [antigo, dados],
                ignore_index=True
            )

        else:

            final = dados

        # salvar

        final.to_excel(
            arquivo_excel,
            index=False
        )

        final.to_csv(
            "exports/resultado.csv",
            index=False
        )

        # resposta

        return {

            "morada": morada if morada else "Não encontrada",

            "codigo_postal": codigo if codigo else "Não encontrado",

            "texto_ocr": texto
        }

    except Exception as e:

        print("\n========== ERRO ==========\n")
        print(str(e))

        return {
            "erro": str(e)
        }

# =========================
# DOWNLOAD EXCEL
# =========================

@app.get("/download-excel")
async def download_excel():

    return FileResponse(
        path="exports/resultado.xlsx",
        filename="resultado.xlsx"
    )

# =========================
# DOWNLOAD CSV
# =========================

@app.get("/download-csv")
async def download_csv():

    return FileResponse(
        path="exports/resultado.csv",
        filename="resultado.csv"
    )