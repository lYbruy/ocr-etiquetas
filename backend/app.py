from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

import os
import re
import cv2
import uuid
import csv
import shutil
import traceback
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

from paddleocr import PaddleOCR


# =========================
# CONFIG
# =========================

PREFIXOS_ACEITES = ["3800", "3810"]

EXPORT_EXCEL = "exports/resultado.xlsx"
EXPORT_CSV = "exports/resultado.csv"

GEOCODER_ENABLED = os.getenv("GEOCODER_ENABLED", "true").lower() == "true"
GEOCODER_TIMEOUT = int(os.getenv("GEOCODER_TIMEOUT", "5"))
GEOCODER_USER_AGENT = os.getenv(
    "GEOCODER_USER_AGENT",
    "ocr-etiquetas-aveiro/1.0"
)

LOCALIDADES_AVEIRO = [
    "AVEIRO", "CACIA", "CÁCIA", "ESGUEIRA", "ARADAS",
    "GLORIA", "GLÓRIA", "VERA CRUZ", "SANTA JOANA",
    "SAO BERNARDO", "SÃO BERNARDO", "OLIVEIRINHA",
    "EIXO", "EIROL", "NARIZ", "REQUEIXO",
    "NOSSA SENHORA DE FATIMA", "NOSSA SENHORA DE FÁTIMA",
]

PALAVRAS_NAO_PORTUGAL = [
    "SPAIN", "ESPAÑA", "ESPANA", "ESPANHA",
    "CALLE ", "CALLE,", " C/ ", "C/.", " CL ",
    "PLAZA ", "PASEO ", "PASE0 ", "CARRER ",
    "AVDA.", "AVDA ", "POL. ", "POL,",
    "POLIGONO", "POLÍGONO",
    "NAVE ", "PARCELA ",
    "P.O. BOX", "APARTADO DE CORREOS",
    "GERMANY", "FRANCE", "ITALIA",
    "UNITED KINGDOM", "NEDERLAND", "BELGIQUE",
]

CIDADES_NAO_PORTUGAL = [
    "MADRID", "BARCELONA", "VALENCIA", "SEVILLA", "ZARAGOZA",
    "MALAGA", "MURCIA", "BILBAO", "ALICANTE", "CORDOBA",
    "VALLADOLID", "VIGO", "GIJON", "VITORIA", "GRANADA",
    "ELCHE", "OVIEDO", "BADALONA", "CARTAGENA", "TERRASSA",
    "SABADELL", "JEREZ", "MOSTOLES", "LEGANES", "BURGOS",
    "SANTANDER", "FUENLABRADA", "ALMERIA", "ALCALA", "PAMPLONA",
    "CADIZ", "SALAMANCA", "TOLEDO", "HUELVA", "BADAJOZ",
    "LOGRONO", "TARRAGONA", "LLEIDA", "GIRONA", "ALBACETE",
    "BERLIN", "PARIS", "LONDON", "ROMA", "AMSTERDAM",
    "BRUSSELS", "LISBOA",
]

PREFIXOS_CP_ESPANHA = set(f"{n:02d}" for n in range(1, 53))


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
# MEMÓRIA DO LOTE
# =========================

ocr_engine = None
uploads_pendentes = {}
lote_confirmado = []
executor = ThreadPoolExecutor(max_workers=3)


# =========================
# MODELOS
# =========================

class ConfirmarPayload(BaseModel):
    upload_id: str | None = None
    morada: str
    codigo_postal: str
    cidade: str | None = ""
    texto_ocr: str | None = ""


# =========================
# OCR GLOBAL
# =========================

def get_ocr():
    global ocr_engine

    if ocr_engine is None:
        print("Inicializando PaddleOCR...", flush=True)

        ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            show_log=False,
            det_db_thresh=0.3,
            det_db_box_thresh=0.5,
            rec_batch_num=6,
        )

        print("PaddleOCR inicializado", flush=True)

    return ocr_engine


@app.on_event("startup")
async def startup_event():
    try:
        limpar_pasta_exports()
        lote_confirmado.clear()
        uploads_pendentes.clear()

        print("Pré-aquecendo OCR...", flush=True)

        engine = get_ocr()

        dummy = np.ones((100, 300, 3), dtype=np.uint8) * 255
        dummy_path = "uploads/_warmup.jpg"

        cv2.imwrite(dummy_path, dummy)
        engine.ocr(dummy_path, cls=False)

        if os.path.exists(dummy_path):
            os.remove(dummy_path)

        print("OCR pré-aquecido com sucesso!", flush=True)

    except Exception:
        traceback.print_exc()


# =========================
# ROTAS TESTE
# =========================

@app.get("/")
async def home():
    return {
        "status": "online",
        "message": "API OCR funcionando",
        "filtro": "Somente códigos postais 3800 e 3810 — moradas Portugal/Aveiro",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# =========================
# NORMALIZAÇÃO
# =========================

def limpar_linha(linha: str) -> str:
    linha = str(linha).strip()
    linha = linha.replace("º", "°")
    linha = linha.replace(" N ", " Nº ")
    linha = linha.replace(" N°", " Nº")
    linha = linha.replace(" N.", " Nº")
    linha = linha.replace(" N0 ", " Nº ")
    linha = linha.replace(" No ", " Nº ")
    linha = linha.replace("N°", "Nº")
    linha = linha.replace("N.", "Nº")
    linha = re.sub(r"\s+", " ", linha)
    return linha.strip()


def normalizar_texto(texto: str) -> str:
    texto = str(texto).replace("\r", "\n")
    texto = re.sub(r"\n+", "\n", texto)

    linhas = []

    for linha in texto.split("\n"):
        linha = limpar_linha(linha)

        if linha:
            linhas.append(linha)

    return "\n".join(linhas)


def corrigir_ocr_para_morada(texto: str) -> str:
    texto = str(texto)

    trocas = {
        "AVEIR0": "AVEIRO",
        "AYEIR0": "AVEIRO",
        "AVElRO": "AVEIRO",
        "AVElR0": "AVEIRO",
        "AVFIR0": "AVEIRO",
        "AVR0": "AVEIRO",
        "AVRO": "AVEIRO",
        "AVR": "AVEIRO",
        "AVARO": "AVEIRO",
        "AYARO": "AVEIRO",
        "AVERO": "AVEIRO",
        "AVE1R0": "AVEIRO",
        "PORTUGA": "PORTUGAL",
        "POR TUGAL": "PORTUGAL",
        "PORTUGALO.C": "PORTUGAL O.C",
        "PORTUGALO.O": "PORTUGAL O.C",
        "CACIA PORTUGALO.C.": "CACIA PORTUGAL",
        "CACIA PORTUGAL-O.C.": "CACIA PORTUGAL",
        "A1AMEDA": "ALAMEDA",
        "A1ameda": "Alameda",
        "S1LVA": "SILVA",
        "Si1va": "Silva",
        "R0CHA": "ROCHA",
        "R0A": "RUA",
        "RU4": "RUA",
        "REPOBLICA": "REPUBLICA",
        "REP0BLICA": "REPUBLICA",
        "REPÚBLICA": "REPUBLICA",
        "NACIONAD": "NACIONAL",
        "NACLONA": "NACIONAL",
        "NACIONA": "NACIONAL",
        "EST.NAC": "ESTRADA NACIONAL",
        "EST NAC": "ESTRADA NACIONAL",
        "EUROPAN": "EUROPA Nº",
        "EUROPA N": "EUROPA Nº",
        "AVENIDAEUROPA": "AVENIDA EUROPA",
        "AVENIDA EUROPA N292": "AVENIDA EUROPA Nº292",
        "AVENIDA EUROPA N°292": "AVENIDA EUROPA Nº292",
        "AVENIDA EUROPA Nº 292": "AVENIDA EUROPA Nº292",
    }

    for errado, certo in trocas.items():
        texto = texto.replace(errado, certo)

    texto = re.sub(
        r"\b(AVENIDA|RUA|ALAMEDA|TRAVESSA|ESTRADA|CAMINHO|LARGO|PRAÇA|PRACA)\s*([A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]+?)N[º°]?\s*(\d+)",
        r"\1 \2 Nº\3",
        texto,
        flags=re.IGNORECASE,
    )

    texto = re.sub(r"\s+", " ", texto)

    return limpar_linha(texto)


def converter_ocr_numero(texto: str) -> str:
    texto = str(texto).upper()
    texto = texto.replace("O", "0").replace("Q", "0").replace("D", "0")
    texto = texto.replace("I", "1").replace("L", "1").replace("|", "1")
    texto = texto.replace("S", "5").replace("B", "8").replace("G", "6")
    return texto


def normalizar_morada_extraida(morada: str) -> str:
    m = corrigir_ocr_para_morada(str(morada)).upper()

    trocas = {
        "REP0BLICA": "REPUBLICA",
        "REPÚBLICA": "REPUBLICA",
        "REPOBLICA": "REPUBLICA",
        "D REPUBLICA": "DA REPUBLICA",
        "DAREPUBLICA": "DA REPUBLICA",
        "DA REP0BLICA": "DA REPUBLICA",
        "DAREPOBLICA": "DA REPUBLICA",
        "DAREPUBLICANR": "DA REPUBLICA NR",
        "DA REPUBLICANR": "DA REPUBLICA NR",
        "DAREPUBLICA NR": "DA REPUBLICA NR",
        "DA REP0BLICANR": "DA REPUBLICA NR",
        "DAREPOBLICANR": "DA REPUBLICA NR",
        "RUADAREPUBLICA": "RUA DA REPUBLICA",
        "RUA DAREPUBLICA": "RUA DA REPUBLICA",
        "RUA DAREP0BLICA": "RUA DA REPUBLICA",
        "RUA DAREPOBLICA": "RUA DA REPUBLICA",
        "PCT DARUADA": "PCT DA RUA DA",
        "PCT DA RUA": "PRACETA DA RUA",
        "PCT": "PRACETA",
        " NR ": " Nº ",
        " NR": " Nº",
        " N ": " Nº ",
        " N.": " Nº",
        " NO ": " Nº ",
        " N0 ": " Nº ",
    }

    for errado, certo in trocas.items():
        m = m.replace(errado, certo)

    m = re.sub(r"([A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ])(\d)", r"\1 \2", m)
    m = re.sub(r"(\d)([A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ])", r"\1 \2", m)

    m = re.sub(r"\bN\s*[º°]?\s*(\d+)", r"Nº \1", m)
    m = re.sub(r"\bNR\s*(\d+)", r"Nº \1", m)

    m = re.sub(r"\s+", " ", m).strip()

    return limpar_linha(m)


# =========================
# PALAVRAS
# =========================

PALAVRAS_MORADA = [
    "RUA", "AVENIDA", "AV.", "ALAMEDA", "TRAVESSA", "LARGO",
    "PRAÇA", "PRACA", "PRACETA", "PCT", "ESTRADA", "ESTRADA NACIONAL",
    "EST.", "EST ", "ESTR.", "EST.NAC", "EST NAC", "NACIONAL",
    "CAMINHO", "URBANIZAÇÃO", "URBANIZACAO", "ROTUNDA",
    "BECO", "BAIRRO", "QUINTA", "LOTE", "ZONA", "R.",
]

PALAVRAS_DESCARTAR = [
    "HTTP", "HTTPS", "APP.COM", "WWW", "ATT:", "OBS",
    "PROCURAR", "YVES", "ROCHER", "COSMETICOS", "COSMÉTICOS",
    "LDA", "S.A", " SA ", "000030038", "SN1", "SNI", "R-",
    "PALPITE", "EXP:", "REF:", "COD BULTO", "BULTO", "PESO",
    "DATA", "FECHA", "REMITENTE", "AMAZON", "SPAIN", "MADRID",
    "TIPO PORTES", "PAGADO", "REEMBOLSO", "ENVIO",
    "1DE1", "1 DE1", "KGS",
]


# =========================
# CÓDIGO POSTAL
# =========================

def codigo_postal_valido(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", str(cp)):
        return False

    prefixo = cp[:4]
    sufixo = cp[5:]

    if prefixo not in PREFIXOS_ACEITES:
        return False

    if int(sufixo) < 1:
        return False

    return True


def texto_tem_localidade_aveiro(texto: str) -> bool:
    t = corrigir_ocr_para_morada(texto).upper()
    return any(loc in t for loc in LOCALIDADES_AVEIRO)


def linha_tem_lixo_para_cp(linha: str) -> bool:
    l = str(linha).upper()

    bloqueios = [
        "HTTP", "APP.COM", "WWW", "ATT:", "OBS", "PROCURAR",
        "PALPITE", "000030038", "EXP:", "REF:", "BULTO",
        "PESO", "COD.", "COD ", "GR:", "NºVEND", "VEND:",
        "DATA:", "HORA:",
        "C.B:", "CB:", "C.D", "CD:", "UL.CLI", "CLI",
        "PAQ", "PAQ24", "63-PAQ", "63PAC",
    ]

    return any(x in l for x in bloqueios)


# =========================
# DETEÇÃO DE MORADAS NÃO-PORTUGUESAS
# =========================

def eh_morada_nao_portugal(linha: str) -> bool:
    l = corrigir_ocr_para_morada(str(linha)).upper()

    if any(p in l for p in PALAVRAS_NAO_PORTUGAL):
        return True

    for cidade in CIDADES_NAO_PORTUGAL:
        if re.search(r"\b" + re.escape(cidade) + r"\b", l):
            return True

    if not re.search(r"\b\d{4}-\d{3}\b", l):
        m5 = re.search(r"\b(\d{5})\b", l)

        if m5:
            cp5 = m5.group(1)
            prefixo2 = cp5[:2]

            if prefixo2 in PREFIXOS_CP_ESPANHA:
                return True

    return False


# =========================
# VALIDAÇÃO ONLINE
# =========================

def validar_morada_online(morada: str, codigo_postal: str) -> dict:
    resultado = {
        "valida": False,
        "servico": "nominatim",
        "display_name": "",
        "motivo": "",
    }

    if not GEOCODER_ENABLED:
        resultado["motivo"] = "geocoder_desativado"
        return resultado

    morada = limpar_linha(corrigir_ocr_para_morada(morada))
    codigo_postal = str(codigo_postal).strip()

    if not morada or morada == "Não encontrada":
        resultado["motivo"] = "morada_vazia"
        return resultado

    if not codigo_postal_valido(codigo_postal):
        resultado["motivo"] = "codigo_postal_invalido"
        return resultado

    if eh_morada_nao_portugal(morada):
        resultado["motivo"] = "morada_nao_portugal"
        return resultado

    try:
        query = f"{morada}, {codigo_postal}, Aveiro, Portugal"

        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "json",
                "addressdetails": 1,
                "limit": 5,
                "countrycodes": "pt",
            },
            headers={
                "User-Agent": GEOCODER_USER_AGENT,
            },
            timeout=GEOCODER_TIMEOUT,
        )

        if response.status_code != 200:
            resultado["motivo"] = f"status_{response.status_code}"
            return resultado

        dados = response.json()

        if not dados:
            resultado["motivo"] = "sem_resultados"
            return resultado

        for item in dados:
            display = str(item.get("display_name", "")).upper()
            address = item.get("address", {}) or {}

            postcode = str(address.get("postcode", "")).strip()
            city = str(
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("municipality")
                or address.get("county")
                or ""
            ).upper()

            country_code = str(address.get("country_code", "")).lower()

            bate_pais = country_code == "pt" or "PORTUGAL" in display
            bate_aveiro = "AVEIRO" in display or "AVEIRO" in city
            bate_cp = (
                postcode.startswith("3800")
                or postcode.startswith("3810")
                or codigo_postal[:4] in display
            )

            if bate_pais and bate_aveiro and bate_cp:
                resultado["valida"] = True
                resultado["display_name"] = item.get("display_name", "")
                resultado["motivo"] = "validada"
                return resultado

        resultado["motivo"] = "resultado_nao_bate_aveiro_cp"
        resultado["display_name"] = dados[0].get("display_name", "")

        return resultado

    except Exception as e:
        resultado["motivo"] = f"erro_geocoder: {str(e)}"
        return resultado


# =========================
# MORADA
# =========================

def eh_linha_cidade(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper().strip()

    if not l:
        return False

    if any(x in l for x in PALAVRAS_DESCARTAR):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    letras = len(re.findall(r"[A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ]", l))
    numeros = len(re.findall(r"\d", l))

    return letras >= 3 and numeros <= 3


def eh_morada_valida(linha: str) -> bool:
    l = normalizar_morada_extraida(linha).upper().strip()

    if not l:
        return False

    if any(x in l for x in PALAVRAS_DESCARTAR):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    tem_palavra_morada = any(p in l for p in PALAVRAS_MORADA)
    tem_numero = bool(re.search(r"\d", l))

    if re.search(r"EST\.?\s*NAC", l) and tem_numero:
        return True

    if re.search(r"ESTRADA\s+NACIONAL", l) and tem_numero:
        return True

    return tem_palavra_morada and tem_numero


def extrair_morada_antes_do_cp_na_linha(linha: str, inicio_cp: int) -> str:
    parte_antes = str(linha)[:inicio_cp].strip()

    if not parte_antes:
        return ""

    parte_antes = normalizar_morada_extraida(parte_antes)

    vias = [
        "RUA", "AVENIDA", "ALAMEDA", "TRAVESSA", "LARGO",
        "PRAÇA", "PRACA", "PRACETA", "PCT", "ESTRADA",
        "CAMINHO", "BECO", "BAIRRO", "QUINTA", "ROTUNDA",
    ]

    parte_upper = parte_antes.upper()
    melhor_pos = None

    for via in vias:
        pos = parte_upper.find(via)

        if pos != -1:
            if melhor_pos is None or pos < melhor_pos:
                melhor_pos = pos

    if melhor_pos is not None:
        parte_antes = parte_antes[melhor_pos:].strip()

    parte_antes = normalizar_morada_extraida(parte_antes)

    if eh_morada_nao_portugal(parte_antes):
        return ""

    if eh_morada_valida(parte_antes):
        return parte_antes

    if any(v in parte_antes.upper() for v in vias) and re.search(r"\d", parte_antes):
        return parte_antes

    return ""


def pontuar_morada(linha: str, index: int, cp_index: int) -> int:
    l = normalizar_morada_extraida(linha).upper()
    score = 0

    if eh_morada_nao_portugal(linha):
        return -9999

    if eh_morada_valida(linha):
        score += 150

    if "AVENIDA" in l or "AV." in l:
        score += 35

    if "RUA" in l or " R." in l:
        score += 35

    if "ALAMEDA" in l:
        score += 25

    if "TRAVESSA" in l:
        score += 20

    if "PRACETA" in l or "PCT" in l:
        score += 20

    if "ESTRADA" in l or "NACIONAL" in l or "EST.NAC" in l:
        score += 30

    if "Nº" in l or "N°" in l:
        score += 18

    if re.search(r"\d", l):
        score += 20

    distancia = abs(cp_index - index)
    score += max(0, 70 - distancia * 10)

    if index < cp_index:
        score += 30

    if index > cp_index:
        score -= 20

    if index < cp_index - 8:
        score -= 35

    if texto_tem_localidade_aveiro(l):
        score += 40

    return score


def _linha_quebra_bloco(linha: str) -> bool:
    l = linha.strip().upper()

    if not l:
        return True

    if re.match(r"^\d{8,}$", l):
        return True

    if re.match(r"^[A-Z]{2}\d{9,}[A-Z]{2}$", l):
        return True

    if re.match(r"^[A-Z0-9]{14,}$", l):
        return True

    LOGISTICA = [
        "REMITENTE", "DESTINATARIO", "DESTINATÁRIO",
        "TIPO PORTES", "REEMBOLSO", "PAGADO", "BULTO",
        "COD BULTO", "1DE1", "1 DE 1", "1 DE1",
        "PESO:", "KGS", "ENVIO", "FECHA",
        "ATT:", "EXP:", "REF:", "OBS:",
        "C.B:", "CB:", "UL.CLI",
    ]

    if any(k in l for k in LOGISTICA):
        return True

    if re.search(r"\b\d{4}[- ]\d{3}\b", l):
        return True

    if re.search(r"(?<!\d)\d{5}(?!\d)", l) and not re.search(r"\b\d{4}[- ]\d{3}\b", l):
        return True

    return False


# =========================
# EXTRAÇÃO DE CÓDIGO POSTAL
# =========================

def extrair_cp_mesma_linha(linha: str) -> dict | None:
    original = str(linha).upper()

    if linha_tem_lixo_para_cp(original):
        return None

    linha_corrigida = corrigir_ocr_para_morada(original)
    linha_num = converter_ocr_numero(linha_corrigida)
    linha_num = linha_num.replace("PT-", "").replace("P-", "").replace("P ", "")

    m = re.search(r"(?<!\d)(3800|3810)\s*[- ]\s*(\d{3})(?!\d)", linha_num)

    if m:
        cp = f"{m.group(1)}-{m.group(2)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
            cidade = limpar_linha(cidade)

            morada_na_linha = extrair_morada_antes_do_cp_na_linha(
                linha_corrigida,
                m.start(),
            )

            return {
                "codigo": cp,
                "cidade": cidade,
                "morada_na_linha": morada_na_linha,
                "origem": "mesma_linha",
            }

    m_compact = re.search(r"(?<!\d)(3800|3810)(\d{3})(?!\d)", linha_num)

    if m_compact:
        cp = f"{m_compact.group(1)}-{m_compact.group(2)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m_compact.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
            cidade = limpar_linha(cidade)

            morada_na_linha = extrair_morada_antes_do_cp_na_linha(
                linha_corrigida,
                m_compact.start(),
            )

            return {
                "codigo": cp,
                "cidade": cidade,
                "morada_na_linha": morada_na_linha,
                "origem": "compacto",
            }

    m_3801 = re.search(r"(?<!\d)3801\s*[- ]\s*(\d{3})(?!\d)", linha_num)

    if m_3801 and any(x in original for x in ["AVEIRO", "AVRO", "AVR0", "AVEI", "ESGUEIRA"]):
        cp = f"3810-{m_3801.group(1)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m_3801.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
            cidade = limpar_linha(cidade)

            morada_na_linha = extrair_morada_antes_do_cp_na_linha(
                linha_corrigida,
                m_3801.start(),
            )

            return {
                "codigo": cp,
                "cidade": cidade,
                "morada_na_linha": morada_na_linha,
                "origem": "corrigido_3801",
            }

    m_3801_compact = re.search(r"(?<!\d)3801(\d{3})(?!\d)", linha_num)

    if m_3801_compact:
        cp = f"3810-{m_3801_compact.group(1)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m_3801_compact.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
            cidade = limpar_linha(cidade)

            morada_na_linha = extrair_morada_antes_do_cp_na_linha(
                linha_corrigida,
                m_3801_compact.start(),
            )

            return {
                "codigo": cp,
                "cidade": cidade,
                "morada_na_linha": morada_na_linha,
                "origem": "compacto_3801",
            }

    return None


def extrair_cp_partido(linhas: list[str], i: int) -> dict | None:
    linha = linhas[i].strip()

    if linha_tem_lixo_para_cp(linha):
        return None

    linha_num = converter_ocr_numero(linha)
    linha_limpa = re.sub(r"[^0-9]", "", linha_num)

    if linha_limpa not in ["3800", "3810", "3801"]:
        return None

    prefixo = "3810" if linha_limpa == "3801" else linha_limpa

    if i + 1 >= len(linhas):
        return None

    prox_original = linhas[i + 1].strip()

    if linha_tem_lixo_para_cp(prox_original):
        return None

    prox_corrigida = corrigir_ocr_para_morada(prox_original)
    prox_num = converter_ocr_numero(prox_corrigida)

    m = re.match(r"^\s*(\d{3})\s*[- ]?\s*(.*)$", prox_num)

    if not m and texto_tem_localidade_aveiro(prox_corrigida):
        m_num = re.search(r"(?<!\d)(\d{3})(?!\d)", prox_num)

        if m_num:
            suffix = m_num.group(1)
            cidade = prox_corrigida.replace(suffix, "")
            cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade.upper())
            cidade = limpar_linha(cidade)

            cp = f"{prefixo}-{suffix}"

            if not codigo_postal_valido(cp):
                return None

            return {
                "codigo": cp,
                "linha_index": i,
                "cidade": cidade,
                "morada_na_linha": "",
                "origem": "partido_localidade",
            }

    if not m:
        return None

    cp = f"{prefixo}-{m.group(1)}"

    if not codigo_postal_valido(cp):
        return None

    cidade = m.group(2).strip()
    cidade = corrigir_ocr_para_morada(cidade)
    cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
    cidade = limpar_linha(cidade)

    return {
        "codigo": cp,
        "linha_index": i,
        "cidade": cidade,
        "morada_na_linha": "",
        "origem": "partido",
    }


def extrair_codigos_postais_aveiro(linhas: list[str]) -> list[dict]:
    encontrados = []

    for i, linha in enumerate(linhas):
        mesmo = extrair_cp_mesma_linha(linha)

        if mesmo:
            cidade = mesmo.get("cidade", "")

            if not cidade and i + 1 < len(linhas):
                prox = linhas[i + 1].strip()

                if eh_linha_cidade(prox):
                    cidade = prox

            encontrados.append({
                "codigo": mesmo["codigo"],
                "linha_index": i,
                "cidade": limpar_linha(corrigir_ocr_para_morada(cidade)),
                "morada_na_linha": mesmo.get("morada_na_linha", ""),
                "origem": mesmo.get("origem", "mesma_linha"),
            })

        partido = extrair_cp_partido(linhas, i)

        if partido:
            encontrados.append(partido)

    melhores = {}

    for item in encontrados:
        chave = item["codigo"]

        if chave not in melhores:
            melhores[chave] = item
            continue

        atual = melhores[chave]

        atual_tem_morada = bool(atual.get("morada_na_linha"))
        novo_tem_morada = bool(item.get("morada_na_linha"))

        if novo_tem_morada and not atual_tem_morada:
            melhores[chave] = item

    return list(melhores.values())


def encontrar_morada_para_codigo(linhas: list[str], cp_info: dict) -> str:
    morada_na_linha = cp_info.get("morada_na_linha", "")

    if morada_na_linha:
        morada_na_linha = normalizar_morada_extraida(morada_na_linha)

        if not eh_morada_nao_portugal(morada_na_linha):
            return morada_na_linha

    cp_index = cp_info["linha_index"]
    candidatos = []

    for i in range(cp_index - 1, max(-1, cp_index - 10), -1):
        linha_raw = linhas[i].strip()
        linha = normalizar_morada_extraida(linha_raw)

        if _linha_quebra_bloco(linha_raw):
            break

        if eh_morada_nao_portugal(linha):
            continue

        if eh_morada_valida(linha):
            score = pontuar_morada(linha, i, cp_index)

            if score > 0:
                candidatos.append({
                    "linha": linha,
                    "score": score,
                    "index": i,
                })

    if not candidatos:
        inicio = max(0, cp_index - 12)

        for i in range(inicio, cp_index):
            linha_raw = linhas[i].strip()
            linha = normalizar_morada_extraida(linha_raw)

            if eh_morada_nao_portugal(linha):
                continue

            if eh_morada_valida(linha):
                score = pontuar_morada(linha, i, cp_index)

                if score > 0:
                    candidatos.append({
                        "linha": linha,
                        "score": score,
                        "index": i,
                    })

    if not candidatos:
        return ""

    candidatos.sort(key=lambda x: x["score"], reverse=True)

    return candidatos[0]["linha"]


def pontuar_resultado(resultado: dict) -> int:
    score = 0

    morada = resultado.get("morada", "")
    codigo = resultado.get("codigo_postal", "")
    cidade = resultado.get("cidade", "")
    contexto = resultado.get("contexto", "")
    origem_codigo = resultado.get("origem_codigo", "")

    if codigo_postal_valido(codigo):
        score += 200

    if morada and morada != "Não encontrada":
        score += 200

    if eh_morada_valida(morada):
        score += 120

    if eh_morada_nao_portugal(morada):
        score -= 9999

    if origem_codigo in ["mesma_linha", "compacto", "corrigido_3801", "compacto_3801"]:
        if morada and morada != "Não encontrada":
            score += 300

    if origem_codigo in ["partido", "partido_localidade"]:
        score -= 80

    if cidade and cidade != "Não encontrada":
        score += 30

    if texto_tem_localidade_aveiro(cidade):
        score += 45

    if texto_tem_localidade_aveiro(contexto):
        score += 45

    texto_contexto = contexto.upper()

    if any(x in texto_contexto for x in ["R-", "SN1", "PALPITE", "ATT:", "OBS", "EXP:", "REF:", "BULTO", "C.B:", "CB:"]):
        score -= 70

    return score


def escolher_destinatario(resultados: list[dict]) -> dict | None:
    validos = [
        r for r in resultados
        if codigo_postal_valido(r["codigo_postal"])
        and r.get("morada", "Não encontrada") != "Não encontrada"
        and not eh_morada_nao_portugal(r.get("morada", ""))
    ]

    if not validos:
        validos = [
            r for r in resultados
            if codigo_postal_valido(r["codigo_postal"])
        ]

    if not validos:
        return None

    validos.sort(key=lambda x: x["score"], reverse=True)

    return validos[0]


def extrair_dados_aveiro(texto: str) -> dict:
    texto = normalizar_texto(texto)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    cps = extrair_codigos_postais_aveiro(linhas)
    resultados = []

    for cp in cps:
        if not codigo_postal_valido(cp["codigo"]):
            continue

        morada = encontrar_morada_para_codigo(linhas, cp)
        cidade = corrigir_ocr_para_morada(cp.get("cidade", ""))
        idx = cp["linha_index"]

        contexto_inicio = max(0, idx - 5)
        contexto_fim = min(len(linhas), idx + 5)
        contexto = "\n".join(linhas[contexto_inicio:contexto_fim])

        item = {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": cp["codigo"],
            "cidade": cidade if cidade else "",
            "linha_codigo_index": idx,
            "origem_codigo": cp.get("origem", ""),
            "contexto": contexto,
            "geo_validada": False,
            "geo_motivo": "",
            "geo_display_name": "",
        }

        item["score"] = pontuar_resultado(item)

        if item["morada"] != "Não encontrada" and codigo_postal_valido(item["codigo_postal"]):
            geo = validar_morada_online(item["morada"], item["codigo_postal"])

            item["geo_validada"] = geo.get("valida", False)
            item["geo_motivo"] = geo.get("motivo", "")
            item["geo_display_name"] = geo.get("display_name", "")

            if geo.get("valida"):
                item["score"] += 250
            else:
                item["score"] -= 80

        resultados.append(item)

    resultados.sort(key=lambda x: x["score"], reverse=True)

    escolhido = escolher_destinatario(resultados)

    if escolhido:
        return {
            "morada": escolhido["morada"],
            "codigo_postal": escolhido["codigo_postal"],
            "cidade": escolhido.get("cidade", ""),
            "geo_validada": escolhido.get("geo_validada", False),
            "geo_motivo": escolhido.get("geo_motivo", ""),
            "geo_display_name": escolhido.get("geo_display_name", ""),
            "todos_resultados": resultados,
        }

    return {
        "morada": "Não encontrada",
        "codigo_postal": "Não encontrado",
        "cidade": "",
        "geo_validada": False,
        "geo_motivo": "",
        "geo_display_name": "",
        "todos_resultados": resultados,
    }


# =========================
# IMAGEM
# =========================

def pre_processar_imagem(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    max_dim = 1600

    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        img = cv2.resize(
            img,
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l = clahe.apply(l)

    lab = cv2.merge((l, a, b))
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    return img


def criar_versoes_imagem(caminho: str) -> list[str]:
    img = cv2.imread(caminho)

    if img is None:
        return [caminho]

    base = str(uuid.uuid4())
    versoes = []

    img_proc = pre_processar_imagem(img.copy())
    proc_path = f"uploads/proc_{base}.jpg"

    cv2.imwrite(proc_path, img_proc, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(proc_path)

    gray = cv2.cvtColor(img_proc, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    kernel = np.array([
        [0, -1, 0],
        [-1, 5, -1],
        [0, -1, 0],
    ])

    gray = cv2.filter2D(gray, -1, kernel)

    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(gray_path)

    blur = cv2.GaussianBlur(gray, (3, 3), 0)

    binary = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        25,
        8,
    )

    bin_path = f"uploads/bin_{base}.jpg"
    cv2.imwrite(bin_path, binary, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(bin_path)

    return versoes


# =========================
# OCR PARSER
# =========================

def extrair_texto_resultado_ocr(resultado) -> str:
    textos = []

    if not resultado:
        return ""

    if isinstance(resultado, list):
        for item in resultado:
            if isinstance(item, dict):
                if "rec_texts" in item and isinstance(item["rec_texts"], list):
                    textos.extend([str(x) for x in item["rec_texts"]])
                elif "text" in item:
                    textos.append(str(item["text"]))

            elif isinstance(item, list):
                for linha in item:
                    try:
                        if isinstance(linha, (list, tuple)) and len(linha) >= 2:
                            data = linha[1]

                            if isinstance(data, (tuple, list)):
                                textos.append(str(data[0]))
                            else:
                                textos.append(str(data))
                    except Exception:
                        pass

    return "\n".join(textos)


def juntar_textos_unicos(textos: list[str]) -> str:
    linhas_final = []
    vistos = set()

    for texto in textos:
        texto = normalizar_texto(texto)

        for linha in texto.split("\n"):
            linha = limpar_linha(linha)

            if not linha:
                continue

            chave = linha.upper()

            if chave not in vistos:
                vistos.add(chave)
                linhas_final.append(linha)

    return "\n".join(linhas_final)


def _ocr_uma_versao(path: str) -> str:
    engine = get_ocr()

    try:
        resultado = engine.ocr(path, cls=True)
        texto = extrair_texto_resultado_ocr(resultado)

        return normalizar_texto(texto)

    except TypeError:
        try:
            resultado = engine.ocr(path)
            texto = extrair_texto_resultado_ocr(resultado)

            return normalizar_texto(texto)

        except Exception:
            traceback.print_exc()
            return ""

    except Exception:
        traceback.print_exc()
        return ""


def rodar_ocr_em_versoes(versoes: list[str]) -> str:
    textos = []

    for i, path in enumerate(versoes):
        print(f"OCR versão {i + 1}/{len(versoes)}: {path}", flush=True)

        texto = _ocr_uma_versao(path)

        if texto:
            print(f"Texto versão {i + 1}:\n{texto}", flush=True)
            textos.append(texto)

    return juntar_textos_unicos(textos)


# =========================
# EXPORTAÇÃO
# =========================

def salvar_lote_em_arquivos(snapshot=None, excel_path=EXPORT_EXCEL, csv_path=EXPORT_CSV):
    dados = snapshot if snapshot is not None else list(lote_confirmado)

    os.makedirs(os.path.dirname(excel_path), exist_ok=True)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Etiquetas Aveiro"

    COR_TITULO_BG = "0F172A"
    COR_HEADER_BG = "1D4ED8"
    COR_HEADER_FONT = "FFFFFF"
    COR_LINHA_PAR = "EEF6FF"
    COR_LINHA_IMPAR = "FFFFFF"
    COR_BORDA = "CBD5E1"
    COR_TEXTO = "0F172A"
    COR_MUTED = "64748B"

    borda = Border(
        left=Side(style="thin", color=COR_BORDA),
        right=Side(style="thin", color=COR_BORDA),
        top=Side(style="thin", color=COR_BORDA),
        bottom=Side(style="thin", color=COR_BORDA),
    )

    ws.merge_cells("A1:B1")
    titulo = ws["A1"]
    titulo.value = "Exportação de Etiquetas — Aveiro"
    titulo.font = Font(name="Arial", bold=True, size=16, color="FFFFFF")
    titulo.fill = PatternFill("solid", fgColor=COR_TITULO_BG)
    titulo.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 36

    ws.merge_cells("A2:B2")
    resumo = ws["A2"]
    resumo.value = f"Total de etiquetas exportadas: {len(dados)}"
    resumo.font = Font(name="Arial", size=11, color=COR_MUTED)
    resumo.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 24

    headers = ["Morada", "Código Postal"]

    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = Font(name="Arial", bold=True, size=12, color=COR_HEADER_FONT)
        cell.fill = PatternFill("solid", fgColor=COR_HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = borda

    ws.row_dimensions[3].height = 28

    for idx, item in enumerate(dados, start=4):
        cor_bg = COR_LINHA_PAR if idx % 2 == 0 else COR_LINHA_IMPAR
        fill = PatternFill("solid", fgColor=cor_bg)

        morada = item.get("Morada", "")
        cp = item.get("Código Postal", "")

        c1 = ws.cell(row=idx, column=1, value=morada)
        c1.font = Font(name="Arial", size=11, color=COR_TEXTO)
        c1.fill = fill
        c1.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c1.border = borda

        c2 = ws.cell(row=idx, column=2, value=cp)
        c2.font = Font(name="Arial", size=11, bold=True, color="1D4ED8")
        c2.fill = fill
        c2.alignment = Alignment(horizontal="center", vertical="center")
        c2.border = borda

        ws.row_dimensions[idx].height = 24

    ws.column_dimensions["A"].width = 62
    ws.column_dimensions["B"].width = 20

    ws.freeze_panes = "A4"

    if dados:
        tabela_ref = f"A3:B{len(dados) + 3}"
        tabela = Table(displayName="TabelaEtiquetasAveiro", ref=tabela_ref)

        estilo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )

        tabela.tableStyleInfo = estilo
        ws.add_table(tabela)

    footer_row = len(dados) + 5

    ws.merge_cells(
        start_row=footer_row,
        start_column=1,
        end_row=footer_row,
        end_column=2,
    )

    footer = ws.cell(row=footer_row, column=1)
    footer.value = "Gerado automaticamente pelo sistema OCR de etiquetas"
    footer.font = Font(name="Arial", italic=True, size=10, color=COR_MUTED)
    footer.alignment = Alignment(horizontal="center")

    wb.save(excel_path)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Morada", "Código Postal"])

        for item in dados:
            writer.writerow([
                item.get("Morada", ""),
                item.get("Código Postal", ""),
            ])


def limpar_ficheiros_exportacao(*paths):
    lote_confirmado.clear()
    uploads_pendentes.clear()

    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def limpar_pasta_exports():
    try:
        os.makedirs("exports", exist_ok=True)

        for nome in os.listdir("exports"):
            caminho = os.path.join("exports", nome)

            if os.path.isfile(caminho):
                os.remove(caminho)

    except Exception:
        pass


# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    caminhos_temporarios = []

    try:
        print("\n========== RECEBEU UPLOAD ==========", flush=True)
        print(f"Arquivo: {file.filename} | Tipo: {file.content_type}", flush=True)

        upload_id = str(uuid.uuid4())
        caminho = f"uploads/{upload_id}.jpg"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        caminhos_temporarios.append(caminho)

        img = cv2.imread(caminho)

        if img is None:
            return {"erro": "Erro ao abrir imagem"}

        print("Criando versões...", flush=True)

        versoes = criar_versoes_imagem(caminho)
        caminhos_temporarios.extend(versoes)

        print("Iniciando OCR...", flush=True)

        texto = rodar_ocr_em_versoes(versoes)

        print("\n========== TEXTO OCR ==========", flush=True)
        print(texto, flush=True)

        dados_extraidos = extrair_dados_aveiro(texto)

        morada = dados_extraidos["morada"]
        codigo = dados_extraidos["codigo_postal"]
        cidade = dados_extraidos.get("cidade", "")

        uploads_pendentes[upload_id] = {
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "texto_ocr": texto,
            "geo_validada": dados_extraidos.get("geo_validada", False),
            "geo_motivo": dados_extraidos.get("geo_motivo", ""),
            "geo_display_name": dados_extraidos.get("geo_display_name", ""),
            "todos_resultados": dados_extraidos["todos_resultados"],
        }

        print(
            f"\nID: {upload_id} | Morada: {morada} | CP: {codigo} | Cidade: {cidade}",
            flush=True,
        )

        return {
            "status": "aguardando_confirmacao",
            "mensagem": "Confirme ou edite os dados antes de adicionar ao lote.",
            "upload_id": upload_id,
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "geo_validada": dados_extraidos.get("geo_validada", False),
            "geo_motivo": dados_extraidos.get("geo_motivo", ""),
            "geo_display_name": dados_extraidos.get("geo_display_name", ""),
            "texto_ocr": texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados_extraidos["todos_resultados"],
            "total_lote": len(lote_confirmado),
            "filtro": "Somente códigos postais 3800 e 3810 — moradas Portugal/Aveiro",
        }

    except Exception as e:
        print("\n========== ERRO ==========", flush=True)
        traceback.print_exc()

        return {"erro": str(e)}

    finally:
        for p in set(caminhos_temporarios):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# =========================
# CONFIRMAR NO LOTE
# =========================

@app.post("/confirmar")
async def confirmar(payload: ConfirmarPayload):
    try:
        morada = normalizar_morada_extraida(payload.morada)
        codigo = payload.codigo_postal.strip()
        cidade = limpar_linha(corrigir_ocr_para_morada(payload.cidade or ""))
        texto_ocr = payload.texto_ocr or ""

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            pendente = uploads_pendentes[payload.upload_id]

            if not texto_ocr:
                texto_ocr = pendente.get("texto_ocr", "")

        if not codigo_postal_valido(codigo):
            return {
                "erro": "Código postal inválido. Este sistema só aceita 3800 ou 3810."
            }

        if not morada or morada == "Não encontrada":
            return {
                "erro": "Morada vazia. Confirme ou escreva a morada correta."
            }

        if eh_morada_nao_portugal(morada):
            return {
                "erro": "A morada indicada não parece ser portuguesa. Por favor, verifique."
            }

        lote_confirmado.append({
            "Morada": morada,
            "Código Postal": codigo,
            "Cidade": cidade,
            "Texto OCR": texto_ocr,
        })

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            del uploads_pendentes[payload.upload_id]

        return {
            "status": "adicionado_ao_lote",
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "total_lote": len(lote_confirmado),
        }

    except Exception as e:
        print("\n========== ERRO AO CONFIRMAR ==========", flush=True)
        traceback.print_exc()

        return {"erro": str(e)}


# =========================
# LOTE
# =========================

@app.get("/resumo-lote")
async def resumo_lote():
    return {
        "total": len(lote_confirmado),
        "itens": lote_confirmado,
    }


@app.post("/limpar-lote")
async def limpar_lote():
    lote_confirmado.clear()
    uploads_pendentes.clear()
    limpar_pasta_exports()

    return {
        "status": "lote_limpo",
        "total": 0,
    }


# =========================
# DOWNLOAD
# =========================

@app.get("/download-excel")
async def download_excel():
    snapshot = list(lote_confirmado)

    export_id = uuid.uuid4().hex
    excel_path = f"exports/resultado_{export_id}.xlsx"
    csv_path = f"exports/resultado_{export_id}.csv"

    salvar_lote_em_arquivos(
        snapshot=snapshot,
        excel_path=excel_path,
        csv_path=csv_path,
    )

    return FileResponse(
        path=excel_path,
        filename="resultado.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(
            limpar_ficheiros_exportacao,
            excel_path,
            csv_path,
        ),
    )


@app.get("/download-csv")
async def download_csv():
    snapshot = list(lote_confirmado)

    export_id = uuid.uuid4().hex
    excel_path = f"exports/resultado_{export_id}.xlsx"
    csv_path = f"exports/resultado_{export_id}.csv"

    salvar_lote_em_arquivos(
        snapshot=snapshot,
        excel_path=excel_path,
        csv_path=csv_path,
    )

    return FileResponse(
        path=csv_path,
        filename="resultado.csv",
        media_type="text/csv",
        background=BackgroundTask(
            limpar_ficheiros_exportacao,
            excel_path,
            csv_path,
        ),
    )