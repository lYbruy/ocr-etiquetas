from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

import os
import re
import cv2
import uuid
import shutil
import traceback
import numpy as np
import pandas as pd
from paddleocr import PaddleOCR
from concurrent.futures import ThreadPoolExecutor


# =========================
# CONFIG
# =========================

PREFIXOS_ACEITES = ["3800", "3810"]

EXPORT_EXCEL = "exports/resultado.xlsx"
EXPORT_CSV   = "exports/resultado.csv"

LOCALIDADES_AVEIRO = [
    "AVEIRO", "CACIA", "CÁCIA", "ESGUEIRA", "ARADAS",
    "GLORIA", "GLÓRIA", "VERA CRUZ", "SANTA JOANA",
    "SAO BERNARDO", "SÃO BERNARDO", "OLIVEIRINHA",
    "EIXO", "EIROL", "NARIZ", "REQUEIXO",
    "NOSSA SENHORA DE FATIMA", "NOSSA SENHORA DE FÁTIMA",
]

# =========================
# PALAVRAS/CIDADES NÃO-PORTUGAL
# Usadas para rejeitar moradas espanholas ou de outros países
# =========================

PALAVRAS_NAO_PORTUGAL = [
    "SPAIN", "ESPAÑA", "ESPANA", "ESPANHA",
    "CALLE ", "CALLE,", " C/ ", "C/.", " CL ",
    "PLAZA ", "PASEO ", "PASE0 ", "CARRER ",
    "AVDA.", "AVDA ", "POL. ", "POL,",
    "POLIGONO", "POLÍGONO",
    "NAVE ", "PARCELA ",
    "P.O. BOX", "APARTADO DE CORREOS",
    "GERMANY", "FRANCE", "ITALIA", "ITALIA",
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
    "BRUSSELS", "LISBOA",  # Lisboa não é Aveiro — fora do filtro
]

# Prefixos de CP espanhóis (2 primeiros dígitos)
# Espanha usa CPs de 5 dígitos: 01000–52999
PREFIXOS_CP_ESPANHA = set(
    f"{n:02d}" for n in range(1, 53)
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
# MEMÓRIA DO LOTE
# =========================

ocr_engine        = None
uploads_pendentes = {}
lote_confirmado   = []
executor          = ThreadPoolExecutor(max_workers=3)

# =========================
# OCR GLOBAL — pré-aquecido no startup
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
    """Pré-aquece o OCR na inicialização para que o primeiro pedido seja rápido."""
    try:
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
# MODELOS
# =========================

class ConfirmarPayload(BaseModel):
    upload_id:     str | None = None
    morada:        str
    codigo_postal: str
    cidade:        str | None = ""
    texto_ocr:     str | None = ""


# =========================
# ROTAS TESTE
# =========================

@app.get("/")
async def home():
    return {"status": "online", "message": "API OCR funcionando", "filtro": "Somente códigos postais 3800 e 3810 — moradas Portugal"}

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
        "AVEIR0": "AVEIRO", "AYEIR0": "AVEIRO", "AVElRO": "AVEIRO",
        "AVElR0": "AVEIRO", "AVFIR0": "AVEIRO", "AVR0": "AVEIRO",
        "AVRO": "AVEIRO", "AVR": "AVEIRO",
        "PORTUGA": "PORTUGAL", "POR TUGAL": "PORTUGAL",
        "CACIA PORTUGALO.C.": "CACIA PORTUGAL",
        "CACIA PORTUGAL-O.C.": "CACIA PORTUGAL",
        "A1AMEDA": "ALAMEDA", "A1ameda": "Alameda",
        "S1LVA": "SILVA", "Si1va": "Silva",
        "R0CHA": "ROCHA", "R0A": "RUA", "RU4": "RUA",
        "NACIONAD": "NACIONAL", "NACLONA": "NACIONAL", "NACIONA": "NACIONAL",
        "EST.NAC": "ESTRADA NACIONAL", "EST NAC": "ESTRADA NACIONAL",
        "EUROPAN": "EUROPA Nº", "EUROPA N": "EUROPA Nº",
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
        texto, flags=re.IGNORECASE,
    )
    texto = re.sub(r"\s+", " ", texto)
    return limpar_linha(texto)


def converter_ocr_numero(texto: str) -> str:
    texto = str(texto).upper()
    texto = texto.replace("O", "0").replace("Q", "0").replace("D", "0")
    texto = texto.replace("I", "1").replace("L", "1").replace("|", "1")
    texto = texto.replace("S", "5").replace("B", "8").replace("G", "6")
    return texto


# =========================
# PALAVRAS
# =========================

PALAVRAS_MORADA = [
    "RUA", "AVENIDA", "AV.", "ALAMEDA", "TRAVESSA", "LARGO",
    "PRAÇA", "PRACA", "ESTRADA", "ESTRADA NACIONAL",
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
# FILTRO CÓDIGO POSTAL
# =========================

def cp_eh_aveiro(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", str(cp)):
        return False
    return cp[:4] in PREFIXOS_ACEITES


def codigo_postal_valido(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", str(cp)):
        return False
    prefixo = cp[:4]
    sufixo  = cp[5:]
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
    ]
    return any(x in l for x in bloqueios)


def extrair_cp_mesma_linha(linha: str) -> dict | None:
    original = str(linha).upper()
    if linha_tem_lixo_para_cp(original):
        return None
    linha_num = converter_ocr_numero(original)
    linha_num = linha_num.replace("PT-", "").replace("P-", "").replace("P ", "")
    m = re.search(r"\b(3800|3810)\s*[- ]\s*(\d{3})\b", linha_num)
    if m:
        cp = f"{m.group(1)}-{m.group(2)}"
        if codigo_postal_valido(cp):
            cidade = linha_num[m.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            return {"codigo": cp, "cidade": cidade, "origem": "mesma_linha"}
    m_3801 = re.search(r"\b3801\s*[- ]\s*(\d{3})\b", linha_num)
    if m_3801 and any(x in original for x in ["AVEIRO", "AVRO", "AVR0", "AVEI"]):
        cp = f"3810-{m_3801.group(1)}"
        if codigo_postal_valido(cp):
            cidade = linha_num[m_3801.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)
            return {"codigo": cp, "cidade": cidade, "origem": "corrigido_3801"}
    return None


def extrair_cp_partido(linhas: list[str], i: int) -> dict | None:
    linha = linhas[i].strip()
    if linha_tem_lixo_para_cp(linha):
        return None
    linha_num   = converter_ocr_numero(linha)
    linha_limpa = re.sub(r"[^0-9]", "", linha_num)
    if linha_limpa not in ["3800", "3810", "3801"]:
        return None
    prefixo = "3810" if linha_limpa == "3801" else linha_limpa
    if i + 1 >= len(linhas):
        return None
    prox_original = linhas[i + 1].strip()
    if linha_tem_lixo_para_cp(prox_original):
        return None
    prox_num = converter_ocr_numero(prox_original)
    m = re.match(r"^\s*(\d{3})\s*[- ]?\s*(.*)$", prox_num)
    if not m:
        return None
    cp = f"{prefixo}-{m.group(1)}"
    if not codigo_postal_valido(cp):
        return None
    cidade = m.group(2).strip()
    cidade = corrigir_ocr_para_morada(cidade)
    cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
    cidade = limpar_linha(cidade)
    return {"codigo": cp, "linha_index": i, "cidade": cidade, "origem": "partido"}


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
                "codigo":      mesmo["codigo"],
                "linha_index": i,
                "cidade":      limpar_linha(corrigir_ocr_para_morada(cidade)),
                "origem":      mesmo.get("origem", "mesma_linha"),
            })
        partido = extrair_cp_partido(linhas, i)
        if partido:
            encontrados.append(partido)
    unicos = []
    vistos = set()
    for item in encontrados:
        chave = item["codigo"]
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)
    return unicos


# =========================
# DETEÇÃO DE MORADAS NÃO-PORTUGUESAS
# =========================

def eh_morada_nao_portugal(linha: str) -> bool:
    """
    Retorna True se a linha contém indicadores claros de morada
    não-portuguesa (espanhola, alemã, francesa, etc.).
    Essas linhas são EXCLUÍDAS do candidato a morada.
    """
    l = corrigir_ocr_para_morada(str(linha)).upper()

    # Palavras-chave de países / tipos de via estrangeiros
    if any(p in l for p in PALAVRAS_NAO_PORTUGAL):
        return True

    # Cidades estrangeiras conhecidas
    for cidade in CIDADES_NAO_PORTUGAL:
        # Match de palavra completa para evitar falsos positivos
        if re.search(r"\b" + re.escape(cidade) + r"\b", l):
            return True

    # Código postal espanhol: 5 dígitos isolados (sem hífen português XXXX-XXX)
    # e que NÃO sejam um prefixo português de 4 dígitos seguido de qualquer coisa
    if not re.search(r"\b\d{4}-\d{3}\b", l):
        m5 = re.search(r"\b(\d{5})\b", l)
        if m5:
            cp5 = m5.group(1)
            # CPs espanhóis: 01000-52999 (primeiros 2 dígitos entre 01 e 52)
            prefixo2 = cp5[:2]
            if prefixo2 in PREFIXOS_CP_ESPANHA:
                return True

    return False


def tem_contexto_portugal(linhas: list[str], cp_index: int, janela: int = 6) -> bool:
    """
    Verifica se há indicadores de Portugal num raio de 'janela' linhas
    em torno do código postal.
    """
    inicio = max(0, cp_index - janela)
    fim    = min(len(linhas), cp_index + janela + 1)
    bloco  = " ".join(linhas[inicio:fim]).upper()
    indicadores = ["PORTUGAL", "AVEIRO", "CACIA", "ESGUEIRA", "ARADAS",
                   "GLORIA", "VERA CRUZ", "SANTA JOANA", "SAO BERNARDO",
                   "OLIVEIRINHA", "EIXO", "EIROL", "NARIZ", "REQUEIXO"]
    return any(ind in bloco for ind in indicadores)


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
    letras  = len(re.findall(r"[A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ]", l))
    numeros = len(re.findall(r"\d", l))
    return letras >= 3 and numeros <= 3


def eh_morada_valida(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper().strip()
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


def pontuar_morada(linha: str, index: int, cp_index: int) -> int:
    l = corrigir_ocr_para_morada(linha).upper()
    score = 0

    # ── Penalidade máxima para moradas não-portuguesas ──────────────────
    if eh_morada_nao_portugal(linha):
        return -9999  # elimina este candidato completamente

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
    if "ESTRADA" in l or "NACIONAL" in l or "EST.NAC" in l:
        score += 30
    if "Nº" in l or "N°" in l:
        score += 18
    if re.search(r"\d", l):
        score += 20

    # ── Bónus de proximidade ao CP ──────────────────────────────────────
    distancia = abs(cp_index - index)
    score += max(0, 70 - distancia * 10)
    if index < cp_index:
        score += 30   # bónus: morada antes do CP (padrão normal)
    if index > cp_index:
        score -= 20
    if index < cp_index - 8:
        score -= 35

    # ── Bónus por contexto de localidade de Aveiro ──────────────────────
    if texto_tem_localidade_aveiro(l):
        score += 40

    return score


def encontrar_morada_para_codigo(linhas: list[str], cp_info: dict) -> str:
    cp_index = cp_info["linha_index"]
    candidatos = []
    inicio = max(0, cp_index - 12)
    fim    = min(len(linhas), cp_index + 3)

    for i in range(inicio, fim):
        linha = limpar_linha(corrigir_ocr_para_morada(linhas[i]))

        # Rejeita imediatamente se for morada não-portuguesa
        if eh_morada_nao_portugal(linha):
            continue

        if eh_morada_valida(linha):
            score = pontuar_morada(linha, i, cp_index)
            if score > 0:  # só adiciona candidatos com pontuação positiva
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
    score    = 0
    morada   = resultado.get("morada", "")
    codigo   = resultado.get("codigo_postal", "")
    cidade   = resultado.get("cidade", "")
    contexto = resultado.get("contexto", "")

    if codigo_postal_valido(codigo):
        score += 200
    if morada and morada != "Não encontrada":
        score += 150
    if eh_morada_valida(morada):
        score += 70
    if eh_morada_nao_portugal(morada):
        score -= 9999  # invalida resultado com morada estrangeira
    if cidade and cidade != "Não encontrada":
        score += 30
    if texto_tem_localidade_aveiro(cidade):
        score += 45
    if texto_tem_localidade_aveiro(contexto):
        score += 45
    score += int(resultado.get("linha_codigo_index", 0)) * 2

    texto_contexto = contexto.upper()
    if any(x in texto_contexto for x in ["R-", "SN1", "PALPITE", "ATT:", "OBS", "EXP:", "REF:", "BULTO"]):
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
        # fallback: aceita sem morada confirmada se o CP for válido
        validos = [r for r in resultados if codigo_postal_valido(r["codigo_postal"])]
    if not validos:
        return None
    validos.sort(key=lambda x: x["score"], reverse=True)
    return validos[0]


def extrair_dados_aveiro(texto: str) -> dict:
    texto  = normalizar_texto(texto)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    cps    = extrair_codigos_postais_aveiro(linhas)
    resultados = []

    for cp in cps:
        if not codigo_postal_valido(cp["codigo"]):
            continue

        morada = encontrar_morada_para_codigo(linhas, cp)
        cidade = corrigir_ocr_para_morada(cp.get("cidade", ""))
        idx    = cp["linha_index"]

        contexto_inicio = max(0, idx - 5)
        contexto_fim    = min(len(linhas), idx + 5)
        contexto = "\n".join(linhas[contexto_inicio:contexto_fim])

        item = {
            "morada":             morada if morada else "Não encontrada",
            "codigo_postal":      cp["codigo"],
            "cidade":             cidade if cidade else "Não encontrada",
            "linha_codigo_index": idx,
            "origem_codigo":      cp.get("origem", ""),
            "contexto":           contexto,
        }
        item["score"] = pontuar_resultado(item)
        resultados.append(item)

    resultados.sort(key=lambda x: x["score"], reverse=True)
    escolhido = escolher_destinatario(resultados)

    if escolhido:
        return {
            "morada":           escolhido["morada"],
            "codigo_postal":    escolhido["codigo_postal"],
            "cidade":           escolhido["cidade"],
            "todos_resultados": resultados,
        }

    return {
        "morada":           "Não encontrada",
        "codigo_postal":    "Não encontrado",
        "cidade":           "Não encontrada",
        "todos_resultados": resultados,
    }


# =========================
# IMAGEM — versões otimizadas  (NÃO MODIFICADO)
# =========================

def pre_processar_imagem(img: np.ndarray) -> np.ndarray:
    """Melhora contraste e nitidez para OCR mais preciso."""
    h, w = img.shape[:2]
    max_dim = 1600
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    lab  = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l     = clahe.apply(l)
    lab   = cv2.merge((l, a, b))
    img   = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    return img


def criar_versoes_imagem(caminho: str) -> list[str]:
    img = cv2.imread(caminho)
    if img is None:
        return [caminho]

    base    = str(uuid.uuid4())
    versoes = []

    img_proc  = pre_processar_imagem(img.copy())
    proc_path = f"uploads/proc_{base}.jpg"
    cv2.imwrite(proc_path, img_proc, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(proc_path)

    gray = cv2.cvtColor(img_proc, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray  = clahe.apply(gray)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    gray   = cv2.filter2D(gray, -1, kernel)
    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(gray_path)

    blur   = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blur, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 25, 8
    )
    bin_path = f"uploads/bin_{base}.jpg"
    cv2.imwrite(bin_path, binary, [cv2.IMWRITE_JPEG_QUALITY, 95])
    versoes.append(bin_path)

    return versoes


# =========================
# OCR PARSER  (NÃO MODIFICADO)
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
    """Corre OCR numa versão de imagem e devolve o texto."""
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


def resultado_tem_cp_valido(texto: str) -> bool:
    """Verifica se o texto já contém um código postal Aveiro válido."""
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    cps = extrair_codigos_postais_aveiro(linhas)
    return any(codigo_postal_valido(cp["codigo"]) for cp in cps)


def rodar_ocr_em_versoes(versoes: list[str]) -> str:
    """
    Corre OCR em cada versão de forma sequencial mas com early-exit:
    se a primeira versão já deu um bom resultado (CP válido + morada),
    não processa as restantes.
    """
    textos = []

    for i, path in enumerate(versoes):
        print(f"OCR versão {i+1}/{len(versoes)}: {path}", flush=True)
        texto = _ocr_uma_versao(path)

        if texto:
            print(f"Texto versão {i+1}:\n{texto}", flush=True)
            textos.append(texto)

            texto_combinado = juntar_textos_unicos(textos)
            if resultado_tem_cp_valido(texto_combinado):
                dados = extrair_dados_aveiro(texto_combinado)
                if (dados["codigo_postal"] != "Não encontrado"
                        and dados["morada"] != "Não encontrada"):
                    print(f"Early-exit na versão {i+1} — resultado completo encontrado.", flush=True)
                    break

    return juntar_textos_unicos(textos)


# =========================
# EXPORTAÇÃO
# =========================

def gerar_dataframe_lote():
    return pd.DataFrame(lote_confirmado, columns=["Morada", "Código Postal", "Cidade", "Texto OCR"])


def salvar_lote_em_arquivos():
    df = gerar_dataframe_lote()
    df.to_excel(EXPORT_EXCEL, index=False)
    df.to_csv(EXPORT_CSV, index=False)


def limpar_lote_depois_exportar():
    lote_confirmado.clear()
    uploads_pendentes.clear()
    try:
        if os.path.exists(EXPORT_EXCEL):
            os.remove(EXPORT_EXCEL)
        if os.path.exists(EXPORT_CSV):
            os.remove(EXPORT_CSV)
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
        caminho   = f"uploads/{upload_id}.jpg"

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
        cidade = dados_extraidos["cidade"]

        uploads_pendentes[upload_id] = {
            "morada":           morada,
            "codigo_postal":    codigo,
            "cidade":           cidade,
            "texto_ocr":        texto,
            "todos_resultados": dados_extraidos["todos_resultados"],
        }

        print(f"\nID: {upload_id} | Morada: {morada} | CP: {codigo} | Cidade: {cidade}", flush=True)

        return {
            "status":           "aguardando_confirmacao",
            "mensagem":         "Confirme ou edite os dados antes de adicionar ao lote.",
            "upload_id":        upload_id,
            "morada":           morada,
            "codigo_postal":    codigo,
            "cidade":           cidade,
            "texto_ocr":        texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados_extraidos["todos_resultados"],
            "total_lote":       len(lote_confirmado),
            "filtro":           "Somente códigos postais 3800 e 3810 — moradas Portugal",
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
        morada    = limpar_linha(corrigir_ocr_para_morada(payload.morada))
        codigo    = payload.codigo_postal.strip()
        cidade    = limpar_linha(corrigir_ocr_para_morada(payload.cidade or ""))
        texto_ocr = payload.texto_ocr or ""

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            pendente = uploads_pendentes[payload.upload_id]
            if not texto_ocr:
                texto_ocr = pendente.get("texto_ocr", "")

        if not codigo_postal_valido(codigo):
            return {"erro": "Código postal inválido. Este sistema só aceita 3800 ou 3810."}

        if not morada or morada == "Não encontrada":
            return {"erro": "Morada vazia. Confirme ou escreva a morada correta."}

        if eh_morada_nao_portugal(morada):
            return {"erro": "A morada indicada não parece ser portuguesa. Por favor, verifique."}

        lote_confirmado.append({
            "Morada":        morada,
            "Código Postal": codigo,
            "Cidade":        cidade,
            "Texto OCR":     texto_ocr,
        })
        salvar_lote_em_arquivos()

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            del uploads_pendentes[payload.upload_id]

        return {
            "status":        "adicionado_ao_lote",
            "morada":        morada,
            "codigo_postal": codigo,
            "cidade":        cidade,
            "total_lote":    len(lote_confirmado),
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
    return {"total": len(lote_confirmado), "itens": lote_confirmado}


@app.post("/limpar-lote")
async def limpar_lote():
    lote_confirmado.clear()
    uploads_pendentes.clear()
    try:
        if os.path.exists(EXPORT_EXCEL):
            os.remove(EXPORT_EXCEL)
        if os.path.exists(EXPORT_CSV):
            os.remove(EXPORT_CSV)
    except Exception:
        pass
    return {"status": "lote_limpo", "total": 0}


# =========================
# DOWNLOAD
# =========================

@app.get("/download-excel")
async def download_excel():
    salvar_lote_em_arquivos()
    return FileResponse(
        path=EXPORT_EXCEL,
        filename="resultado.xlsx",
        background=BackgroundTask(limpar_lote_depois_exportar)
    )


@app.get("/download-csv")
async def download_csv():
    salvar_lote_em_arquivos()
    return FileResponse(
        path=EXPORT_CSV,
        filename="resultado.csv",
        background=BackgroundTask(limpar_lote_depois_exportar)
    )