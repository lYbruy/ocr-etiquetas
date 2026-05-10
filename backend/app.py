from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import shutil
import cv2
import pandas as pd
import os
import uuid
import re
import traceback

from paddleocr import PaddleOCR

# =========================
# OCR
# =========================

ocr = PaddleOCR(
    use_angle_cls=True,
    lang="en"
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
# PASTAS
# =========================

os.makedirs("uploads", exist_ok=True)
os.makedirs("exports", exist_ok=True)

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
    match = re.search(
        r"\d{4}-\d{3}",
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
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    try:
        print("\n========== RECEBEU UPLOAD ==========", flush=True)
        print(f"Arquivo: {file.filename}", flush=True)
        print(f"Tipo: {file.content_type}", flush=True)

        # salvar foto
        nome = f"{uuid.uuid4()}.jpg"
        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(
                file.file,
                buffer
            )

        print(f"Imagem salva em: {caminho}", flush=True)

        # abrir imagem
        img = cv2.imread(caminho)

        if img is None:
            print("Erro: cv2 não conseguiu abrir a imagem", flush=True)

            return {
                "erro": "Erro ao abrir imagem"
            }

        print("Imagem aberta com sucesso", flush=True)

        # melhorar imagem
        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

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

        cv2.imwrite(
            temp_path,
            gray
        )

        print(f"Imagem processada salva em: {temp_path}", flush=True)

        # OCR
        print("Iniciando OCR...", flush=True)

        resultado = ocr.ocr(
            temp_path,
            cls=True
        )

        print("OCR finalizado", flush=True)

        texto = ""

        if resultado:
            for bloco in resultado:
                if bloco:
                    for linha in bloco:
                        try:
                            txt = linha[1][0]
                            texto += txt + "\n"
                        except Exception:
                            pass

        texto = limpar_texto(texto)

        print("\n========== TEXTO OCR ==========\n", flush=True)
        print(texto, flush=True)

        # extrair dados
        codigo = extrair_codigo(texto)
        morada = extrair_morada(texto)

        print("\n========== DADOS EXTRAÍDOS ==========", flush=True)
        print(f"Morada: {morada}", flush=True)
        print(f"Código Postal: {codigo}", flush=True)

        # dataframe
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

        # salvar
        final.to_excel(
            arquivo_excel,
            index=False
        )

        final.to_csv(
            arquivo_csv,
            index=False
        )

        print("Arquivos Excel/CSV salvos com sucesso", flush=True)

        # apagar temporário
        try:
            os.remove(temp_path)
        except Exception:
            pass

        # resposta
        return {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": codigo if codigo else "Não encontrado",
            "texto_ocr": texto if texto else "Nenhum texto encontrado"
        }

    except Exception as e:
        print("\n========== ERRO ==========\n", flush=True)
        traceback.print_exc()

        return {
            "erro": str(e)
        }

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