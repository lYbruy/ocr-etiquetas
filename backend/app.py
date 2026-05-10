from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import shutil
import cv2
import pandas as pd
import os
import uuid
import re
import requests

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

    lixo = [
        "REEN",
        "REEK",
        "REF",
        "TIPO",
        "COD",
        "BUL",
        "YPO",
        "PEF",
        "EXP",
        "FECHA",
        "|",
        ":",
        ";"
    ]

    for l in lixo:
        texto = texto.replace(l, "")

    return texto.strip()

# =========================
# EXTRAIR CÓDIGO POSTAL
# =========================

def extrair_codigo_postal(texto):

    match = re.search(r"\d{4}-\d{3}", texto)

    if match:
        return match.group()

    match = re.search(r"\d{4}\s?\d{3}", texto)

    if match:

        codigo = match.group()

        codigo = codigo.replace(
            " ",
            "-"
        )

        return codigo

    return ""

# =========================
# EXTRAIR MORADA
# =========================

def extrair_morada(texto):

    linhas = texto.split("\n")

    for linha in linhas:

        linha = linha.strip()

        if (
            "RUA" in linha.upper()
            or "AVENIDA" in linha.upper()
            or "ALAMEDA" in linha.upper()
            or "TRAVESSA" in linha.upper()
            or "ESTRADA" in linha.upper()
        ):

            return linha

    return ""

# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    try:

        # =========================
        # SALVAR FOTO
        # =========================

        nome = f"{uuid.uuid4()}.jpg"

        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # =========================
        # ABRIR IMAGEM
        # =========================

        img = cv2.imread(caminho)

        if img is None:

            return {
                "erro": "Erro ao abrir imagem"
            }

        # =========================
        # REDIMENSIONAR
        # =========================

        img = cv2.resize(
            img,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC
        )

        # =========================
        # PROCESSAR IMAGEM
        # =========================

        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

        gray = cv2.GaussianBlur(
            gray,
            (3, 3),
            0
        )

        gray = cv2.threshold(
            gray,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]

        # =========================
        # OCR ONLINE
        # =========================

        _, img_encoded = cv2.imencode(
            '.jpg',
            gray
        )

        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={
                'filename': img_encoded.tobytes()
            },
            data={
                'apikey': 'helloworld',
                'language': 'eng',
            },
        )

        resultado = response.json()

        print("\n========== OCR RAW ==========\n")
        print(resultado)

        texto = ""

        if (
            "ParsedResults" in resultado
            and len(resultado["ParsedResults"]) > 0
        ):

            texto = resultado["ParsedResults"][0].get(
                "ParsedText",
                ""
            )

        texto = limpar_texto(texto)

        print("\n========== OCR ==========\n")
        print(texto)

        # =========================
        # EXTRAIR DADOS
        # =========================

        codigo_postal = extrair_codigo_postal(texto)

        morada = extrair_morada(texto)

        # =========================
        # FALLBACK
        # =========================

        if not codigo_postal:

            if "3800" in texto:
                codigo_postal = "3800"

        # =========================
        # DATAFRAME
        # =========================

        dados = pd.DataFrame([{
            "Morada": morada,
            "Código Postal": codigo_postal
        }])

        arquivo_excel = "exports/resultado.xlsx"

        # =========================
        # CONCATENAR
        # =========================

        if os.path.exists(arquivo_excel):

            antigo = pd.read_excel(
                arquivo_excel
            )

            final = pd.concat(
                [antigo, dados],
                ignore_index=True
            )

        else:

            final = dados

        # =========================
        # SALVAR
        # =========================

        final.to_excel(
            arquivo_excel,
            index=False
        )

        final.to_csv(
            "exports/resultado.csv",
            index=False
        )

        # =========================
        # RESPOSTA
        # =========================

        return {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": codigo_postal if codigo_postal else "Não encontrado",
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