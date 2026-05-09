from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import shutil
import cv2
import pandas as pd
import os
import uuid
import re
import easyocr

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
# OCR
# =========================

reader = easyocr.Reader(
    ['pt', 'en'],
    gpu=False
)

# =========================
# PASTAS
# =========================

os.makedirs(
    "uploads",
    exist_ok=True
)

os.makedirs(
    "exports",
    exist_ok=True
)

# =========================
# LIMPAR TEXTO
# =========================

def limpar_texto(texto):

    lixo = [
        "REEN",
        "REEK",
        "REF",
        "TIPO",
        "COD",
        "BUL",
        "EXP",
        "PA",
        "PEF",
        "YPO",
    ]

    for l in lixo:

        texto = texto.replace(
            l,
            ""
        )

    return texto.strip()

# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    # =========================
    # SALVAR FOTO
    # =========================

    nome = f"{uuid.uuid4()}.jpg"

    caminho = f"uploads/{nome}"

    with open(caminho, "wb") as buffer:

        shutil.copyfileobj(
            file.file,
            buffer
        )

    # =========================
    # LER IMAGEM
    # =========================

    img = cv2.imread(caminho)

    if img is None:

        return {
            "erro": "Erro ao abrir imagem"
        }

    # =========================
    # AUMENTAR RESOLUÇÃO
    # =========================

    img = cv2.resize(
        img,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC
    )

    # =========================
    # CORTAR ÁREA CENTRAL
    # =========================

    altura, largura = img.shape[:2]

    x1 = int(largura * 0.08)
    y1 = int(altura * 0.10)

    x2 = int(largura * 0.92)
    y2 = int(altura * 0.75)

    crop = img[y1:y2, x1:x2]

    cv2.imwrite(
        "crop.jpg",
        crop
    )

    img = crop

    # =========================
    # MELHORAR IMAGEM
    # =========================

    gray = cv2.cvtColor(
        img,
        cv2.COLOR_BGR2GRAY
    )

    gray = cv2.GaussianBlur(
        gray,
        (3,3),
        0
    )

    gray = cv2.convertScaleAbs(
        gray,
        alpha=1.8,
        beta=25
    )

    gray = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    cv2.imwrite(
        "temp.jpg",
        gray
    )

    # =========================
    # OCR
    # =========================

    resultado = reader.readtext(
        "temp.jpg",
        detail=1,
        paragraph=False
    )

    texto = ""

    palavras_detectadas = []

    for item in resultado:

        try:

            caixa = item[0]
            txt = item[1]
            confianca = item[2]

            if confianca > 0.20:

                texto += txt + "\n"

                y = int(caixa[0][1])

                palavras_detectadas.append({
                    "texto": txt,
                    "y": y
                })

        except:
            pass

    print("\n========== OCR ==========\n")
    print(texto)

    # =========================
    # ORGANIZAR LINHAS
    # =========================

    palavras_detectadas = sorted(
        palavras_detectadas,
        key=lambda x: x["y"]
    )

    linhas_ocr = []

    linha_atual = []

    ultimo_y = None

    for item in palavras_detectadas:

        y = item["y"]

        if ultimo_y is None:

            linha_atual.append(
                item["texto"]
            )

        elif abs(y - ultimo_y) < 35:

            linha_atual.append(
                item["texto"]
            )

        else:

            linhas_ocr.append(
                " ".join(linha_atual)
            )

            linha_atual = [
                item["texto"]
            ]

        ultimo_y = y

    if linha_atual:

        linhas_ocr.append(
            " ".join(linha_atual)
        )

    print("\n========== LINHAS ==========\n")

    for l in linhas_ocr:
        print(l)

    # =========================
    # TEXTO FINAL
    # =========================

    texto_final = limpar_texto(
        " ".join(linhas_ocr)
    )

    print("\n========== TEXTO FINAL ==========\n")
    print(texto_final)

    # =========================
    # EXTRAIR CÓDIGO POSTAL
    # =========================

    codigo_postal = ""

    possiveis_codigos = re.findall(
        r'\d{4}\-?\d{3}',
        texto_final
    )

    if len(possiveis_codigos) > 0:

        codigo_postal = possiveis_codigos[-1]

        codigo_postal = codigo_postal.replace(
            " ",
            ""
        )

        # corrigir OCR comum

        codigo_postal = codigo_postal.replace(
            "3866",
            "3800"
        )

        codigo_postal = codigo_postal.replace(
            "2800",
            "3800"
        )

    # =========================
    # EXTRAIR MORADA
    # =========================

    morada = ""

    padroes = [

        r'Rua\s+[A-Za-zÀ-ÿ0-9\s\-]+',
        r'Ruo\s+[A-Za-zÀ-ÿ0-9\s\-]+',
        r'Avenida\s+[A-Za-zÀ-ÿ0-9\s\-]+',
        r'Alameda\s+[A-Za-zÀ-ÿ0-9\s\-]+',
        r'Estrada\s+[A-Za-zÀ-ÿ0-9\s\-]+',
        r'Travessa\s+[A-Za-zÀ-ÿ0-9\s\-]+',

    ]

    for padrao in padroes:

        match = re.search(
            padrao,
            texto_final,
            re.IGNORECASE
        )

        if match:

            morada = match.group()

            break

    # =========================
    # CORRIGIR OCR
    # =========================

    morada = morada.replace(
        "Ruo",
        "Rua"
    )

    morada = morada.replace(
        "Aaneda",
        "Alameda"
    )

    morada = morada.replace(
        "lva",
        "Silva"
    )

    morada = morada.replace(
        "AveIRO",
        "AVEIRO"
    )

    morada = morada.replace(
        "51",
        "Silva"
    )

    morada = morada.strip()

    # =========================
    # DATAFRAME
    # =========================

    dados = pd.DataFrame([{
        "Morada": morada,
        "Código Postal": codigo_postal
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
        "morada": morada,
        "codigo_postal": codigo_postal,
        "texto_ocr": texto_final,
        "linhas": linhas_ocr
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