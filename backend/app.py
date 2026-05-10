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
# OCR LAZY
# =========================

ocr = None


def get_ocr():
    global ocr

    if ocr is None:
        print("Inicializando PaddleOCR...", flush=True)

        ocr = PaddleOCR(
            use_angle_cls=True,
            lang="en"
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
# NORMALIZAÇÃO
# =========================

def normalizar_texto_ocr(texto: str) -> str:
    texto = texto.replace("\r", "\n")
    texto = texto.replace("|", " ")
    texto = texto.replace(";", " ")
    texto = texto.replace("º", "°")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n+", "\n", texto)
    return texto.strip()


def normalizar_linha(linha: str) -> str:
    linha = linha.strip()

    trocas = {
        " A1AMEDA ": " ALAMEDA ",
        " A1ameda ": " Alameda ",
        " SI1VA ": " SILVA ",
        " Si1va ": " Silva ",
        " AVEIR0": " AVEIRO",
        "AVEIR0": "AVEIRO",
        " MA1A": " MAIA",
        "POR TUGAL": "PORTUGAL",
        "PRACA": "PRAÇA",
        "AVENIDAEUROPA": "AVENIDA EUROPA",
        "AVENIDAEUROPAN": "AVENIDA EUROPA N",
        "AVENIDA EUROPA N°": "AVENIDA EUROPA N° ",
        "AVENIDA EUROPA N": "AVENIDA EUROPA N ",
    }

    linha_corrigida = f" {linha} "

    for errado, certo in trocas.items():
        linha_corrigida = re.sub(
            re.escape(errado),
            certo,
            linha_corrigida,
            flags=re.IGNORECASE
        )

    linha_corrigida = re.sub(r"\s+", " ", linha_corrigida).strip()

    return linha_corrigida


def limpar_linhas(texto: str) -> list[str]:
    linhas = []

    for linha in texto.split("\n"):
        linha = normalizar_linha(linha)

        if not linha:
            continue

        linhas.append(linha)

    return linhas


# =========================
# FILTROS
# =========================

def linha_lixo(linha: str) -> bool:
    u = linha.upper()

    lixo = [
        "HTTP",
        "WWW.",
        "APP.COM",
        "PROCURAR",
        "OBS",
        "ATT:",
        "ATT ",
        "PORTUGAL O.C",
        "POR TUGAL",
        "SUS",
        "Q PROCURAR",
    ]

    if any(x in u for x in lixo):
        return True

    # UUID / links / IDs enormes
    if re.search(r"[a-f0-9]{6,}-[a-f0-9]{4,}", u.lower()):
        return True

    # só número muito grande
    if re.fullmatch(r"\d{6,}", u):
        return True

    return False


def parece_morada(linha: str) -> bool:
    u = linha.upper()

    palavras = [
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
        "URBANIZACAO",
        "ROTUNDA",
        "BECO",
        "BAIRRO",
        "QUINTA",
        "R.",
    ]

    return any(p in u for p in palavras)


def parece_codigo_postal_linha(linha: str) -> bool:
    return bool(re.search(r"\b\d{4}-\d{3}\b", linha))


# =========================
# EXTRAÇÃO DE CÓDIGOS PORTUGAL
# =========================

def reconstruir_codigos_postais(linhas: list[str]) -> list[dict]:
    """
    Detecta códigos postais portugueses reais:
    - 3800-385 AVEIRO
    - 3800 / 974-AVEIRO em linhas separadas
    - 3800 974 AVEIRO
    """

    encontrados = []

    for i, linha in enumerate(linhas):
        linha_upper = linha.upper()

        # Caso normal: 3800-385 AVEIRO
        for match in re.finditer(r"\b(\d{4})[-\s](\d{3})\b", linha_upper):
            codigo = f"{match.group(1)}-{match.group(2)}"

            # ignora coisas tipo 0000-300 vindo de ID
            if codigo.startswith("0000"):
                continue

            localidade = linha_upper[match.end():].strip(" -,.")
            encontrados.append({
                "codigo": codigo,
                "localidade": localidade,
                "linha_codigo_index": i,
                "linha_codigo": linha
            })

        # Caso quebrado:
        # linha: 3800
        # próxima: 974-AVEIRO
        if re.fullmatch(r"\d{4}", linha_upper):
            if i + 1 < len(linhas):
                prox = linhas[i + 1].upper()

                m = re.match(r"^(\d{3})[-\s]*([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]+)", prox)

                if m:
                    codigo = f"{linha_upper}-{m.group(1)}"

                    if not codigo.startswith("0000"):
                        localidade = m.group(2).strip(" -,.")
                        encontrados.append({
                            "codigo": codigo,
                            "localidade": localidade,
                            "linha_codigo_index": i,
                            "linha_codigo": f"{linha} {linhas[i + 1]}"
                        })

        # Caso junto: 3800 974 AVEIRO
        m2 = re.search(r"\b(\d{4})\s+(\d{3})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,})", linha_upper)

        if m2:
            codigo = f"{m2.group(1)}-{m2.group(2)}"

            if not codigo.startswith("0000"):
                localidade = m2.group(3).strip(" -,.")
                encontrados.append({
                    "codigo": codigo,
                    "localidade": localidade,
                    "linha_codigo_index": i,
                    "linha_codigo": linha
                })

    # remover duplicados
    unicos = []
    vistos = set()

    for item in encontrados:
        chave = item["codigo"]

        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)

    return unicos


# =========================
# EXTRAÇÃO DE MORADAS
# =========================

def encontrar_morada_para_codigo(linhas: list[str], index_codigo: int) -> str:
    """
    Procura a morada mais provável perto do código postal.
    Dá prioridade para linhas ANTES do código postal.
    """

    candidatos = []

    inicio = max(0, index_codigo - 8)
    fim = min(len(linhas), index_codigo + 3)

    for i in range(inicio, fim):
        linha = linhas[i]

        if linha_lixo(linha):
            continue

        if parece_codigo_postal_linha(linha):
            continue

        if re.fullmatch(r"\d{3,}", linha):
            continue

        if parece_morada(linha):
            distancia = abs(index_codigo - i)

            score = 100 - distancia

            u = linha.upper()

            if "RUA" in u:
                score += 20

            if "AVENIDA" in u or "AV." in u:
                score += 20

            if any(char.isdigit() for char in linha):
                score += 10

            candidatos.append((score, i, linha))

    if candidatos:
        candidatos.sort(reverse=True)
        return candidatos[0][2]

    return ""


def limpar_morada_final(morada: str) -> str:
    morada = normalizar_linha(morada)

    morada = re.sub(r"\s+", " ", morada)
    morada = morada.strip(" -,.:")

    # Corrigir casos sem espaço
    morada = re.sub(
        r"\bAVENIDA([A-ZÁÉÍÓÚÂÊÔÃÕÇ])",
        r"AVENIDA \1",
        morada,
        flags=re.IGNORECASE
    )

    morada = re.sub(
        r"\bRUA([A-ZÁÉÍÓÚÂÊÔÃÕÇ])",
        r"RUA \1",
        morada,
        flags=re.IGNORECASE
    )

    return morada.strip()


def extrair_moradas_codigos_portugal(texto: str) -> list[dict]:
    linhas = limpar_linhas(texto)

    codigos = reconstruir_codigos_postais(linhas)

    resultados = []

    for item in codigos:
        codigo = item["codigo"]
        localidade = item["localidade"]
        index_codigo = item["linha_codigo_index"]

        morada = encontrar_morada_para_codigo(linhas, index_codigo)
        morada = limpar_morada_final(morada)

        if not morada:
            continue

        resultados.append({
            "morada": morada,
            "codigo_postal": codigo,
            "localidade": localidade
        })

    # remover duplicados por morada + código
    finais = []
    vistos = set()

    for r in resultados:
        chave = (
            r["morada"].upper(),
            r["codigo_postal"]
        )

        if chave not in vistos:
            vistos.add(chave)
            finais.append(r)

    return finais


# =========================
# IMAGEM
# =========================

def criar_versoes_imagem(caminho: str) -> list[str]:
    img = cv2.imread(caminho)

    if img is None:
        return []

    versoes = [caminho]

    base_id = str(uuid.uuid4())

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    gray_path = f"uploads/gray_{base_id}.jpg"
    cv2.imwrite(gray_path, gray)
    versoes.append(gray_path)

    resized = cv2.resize(
        gray,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_CUBIC
    )

    resized_path = f"uploads/resized_{base_id}.jpg"
    cv2.imwrite(resized_path, resized)
    versoes.append(resized_path)

    thresh = cv2.threshold(
        resized,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    thresh_path = f"uploads/thresh_{base_id}.jpg"
    cv2.imwrite(thresh_path, thresh)
    versoes.append(thresh_path)

    sharp = cv2.GaussianBlur(resized, (0, 0), 3)
    sharp = cv2.addWeighted(resized, 1.5, sharp, -0.5, 0)

    sharp_path = f"uploads/sharp_{base_id}.jpg"
    cv2.imwrite(sharp_path, sharp)
    versoes.append(sharp_path)

    return versoes


# =========================
# OCR PARSER
# =========================

def extrair_texto_resultado_ocr(resultado) -> str:
    textos = []

    if not resultado:
        return ""

    try:
        for bloco in resultado:
            if not bloco:
                continue

            for linha in bloco:
                try:
                    txt = linha[1][0]
                    if txt:
                        textos.append(str(txt))
                except Exception:
                    pass

    except Exception:
        pass

    return "\n".join(textos)


def rodar_ocr_em_versoes(versoes: list[str]) -> str:
    engine = get_ocr()

    textos = []

    for path in versoes:
        try:
            print(f"Tentando OCR em: {path}", flush=True)

            resultado = engine.ocr(path)

            texto = extrair_texto_resultado_ocr(resultado)
            texto = normalizar_texto_ocr(texto)

            if texto:
                print("Texto encontrado nesta versão:", flush=True)
                print(texto, flush=True)
                textos.append(texto)

        except Exception:
            print("Erro ao tentar OCR nessa versão", flush=True)
            traceback.print_exc()

    texto_final = "\n".join(textos)
    texto_final = normalizar_texto_ocr(texto_final)

    return texto_final


# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    try:
        print("\n========== RECEBEU UPLOAD ==========", flush=True)
        print(f"Arquivo: {file.filename}", flush=True)
        print(f"Tipo: {file.content_type}", flush=True)

        nome = f"{uuid.uuid4()}.jpg"
        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        print(f"Imagem salva em: {caminho}", flush=True)

        img = cv2.imread(caminho)

        if img is None:
            return {
                "erro": "Erro ao abrir imagem"
            }

        print("Imagem aberta com sucesso", flush=True)
        print("Criando versões da imagem para OCR...", flush=True)

        versoes = criar_versoes_imagem(caminho)

        print("Iniciando OCR...", flush=True)

        texto = rodar_ocr_em_versoes(versoes)

        print("OCR finalizado", flush=True)
        print("\n========== TEXTO OCR ==========", flush=True)
        print(texto, flush=True)

        resultados = extrair_moradas_codigos_portugal(texto)

        print("\n========== DADOS EXTRAÍDOS ==========", flush=True)

        for item in resultados:
            print(
                f"Morada: {item['morada']} | Código: {item['codigo_postal']} | Localidade: {item['localidade']}",
                flush=True
            )

        if not resultados:
            return {
                "erro": "Não encontrei morada e código postal válidos de Portugal.",
                "texto_ocr": texto,
                "resultados": []
            }

        df_novo = pd.DataFrame(resultados)

        arquivo_excel = "exports/resultado.xlsx"
        arquivo_csv = "exports/resultado.csv"

        if os.path.exists(arquivo_excel):
            antigo = pd.read_excel(arquivo_excel)

            df_final = pd.concat(
                [antigo, df_novo],
                ignore_index=True
            )

            df_final = df_final.drop_duplicates(
                subset=["morada", "codigo_postal"],
                keep="last"
            )

        else:
            df_final = df_novo

        df_final.to_excel(
            arquivo_excel,
            index=False
        )

        df_final.to_csv(
            arquivo_csv,
            index=False
        )

        print("Arquivos Excel/CSV salvos com sucesso", flush=True)

        # limpar imagens processadas
        for path in versoes:
            try:
                if path != caminho and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        primeiro = resultados[0]

        return {
            "morada": primeiro["morada"],
            "codigo_postal": primeiro["codigo_postal"],
            "localidade": primeiro["localidade"],
            "resultados": resultados,
            "texto_ocr": texto
        }

    except Exception as e:
        print("\n========== ERRO ==========", flush=True)
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
        pd.DataFrame(
            columns=[
                "morada",
                "codigo_postal",
                "localidade"
            ]
        ).to_excel(
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
        pd.DataFrame(
            columns=[
                "morada",
                "codigo_postal",
                "localidade"
            ]
        ).to_csv(
            arquivo_csv,
            index=False
        )

    return FileResponse(
        path=arquivo_csv,
        filename="resultado.csv"
    )