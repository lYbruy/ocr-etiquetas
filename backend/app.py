from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import os
import re
import cv2
import uuid
import shutil
import traceback
import pandas as pd
from paddleocr import PaddleOCR


# =========================
# CONFIG
# =========================

PREFIXOS_ACEITES = ["3800", "3810"]

EXPORT_EXCEL = "exports/resultado.xlsx"
EXPORT_CSV = "exports/resultado.csv"

LOCALIDADES_AVEIRO = [
    "AVEIRO",
    "CACIA",
    "CÁCIA",
    "ESGUEIRA",
    "ARADAS",
    "GLORIA",
    "GLÓRIA",
    "VERA CRUZ",
    "SANTA JOANA",
    "SAO BERNARDO",
    "SÃO BERNARDO",
    "OLIVEIRINHA",
    "EIXO",
    "EIROL",
    "NARIZ",
    "REQUEIXO",
    "NOSSA SENHORA DE FATIMA",
    "NOSSA SENHORA DE FÁTIMA",
]


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
            show_log=False
        )

        print("PaddleOCR inicializado", flush=True)

    return ocr_engine


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
# ROTAS TESTE
# =========================

@app.get("/")
async def home():
    return {
        "status": "online",
        "message": "API OCR funcionando",
        "filtro": "Somente códigos postais 3800 e 3810"
    }


@app.get("/health")
async def health():
    return {
        "status": "ok"
    }


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
        "PORTUGA": "PORTUGAL",
        "POR TUGAL": "PORTUGAL",
        "CACIA PORTUGALO.C.": "CACIA PORTUGAL",
        "CACIA PORTUGAL-O.C.": "CACIA PORTUGAL",
        "A1AMEDA": "ALAMEDA",
        "A1ameda": "Alameda",
        "S1LVA": "SILVA",
        "Si1va": "Silva",
        "R0CHA": "ROCHA",
        "R0A": "RUA",
        "RU4": "RUA",
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
        flags=re.IGNORECASE
    )

    texto = re.sub(r"\s+", " ", texto)

    return limpar_linha(texto)


def converter_ocr_numero(texto: str) -> str:
    texto = str(texto).upper()

    texto = texto.replace("O", "0")
    texto = texto.replace("Q", "0")
    texto = texto.replace("D", "0")
    texto = texto.replace("I", "1")
    texto = texto.replace("L", "1")
    texto = texto.replace("|", "1")
    texto = texto.replace("S", "5")
    texto = texto.replace("B", "8")
    texto = texto.replace("G", "6")

    return texto


# =========================
# PALAVRAS
# =========================

PALAVRAS_MORADA = [
    "RUA",
    "AVENIDA",
    "AV.",
    "ALAMEDA",
    "TRAVESSA",
    "LARGO",
    "PRAÇA",
    "PRACA",
    "ESTRADA",
    "ESTRADA NACIONAL",
    "EST.",
    "EST ",
    "ESTR.",
    "EST.NAC",
    "EST NAC",
    "NACIONAL",
    "CAMINHO",
    "URBANIZAÇÃO",
    "URBANIZACAO",
    "ROTUNDA",
    "BECO",
    "BAIRRO",
    "QUINTA",
    "LOTE",
    "ZONA",
    "R.",
]

PALAVRAS_DESCARTAR = [
    "HTTP",
    "HTTPS",
    "APP.COM",
    "WWW",
    "ATT:",
    "OBS",
    "PROCURAR",
    "YVES",
    "ROCHER",
    "COSMETICOS",
    "COSMÉTICOS",
    "LDA",
    "S.A",
    " SA ",
    "000030038",
    "SN1",
    "SNI",
    "R-",
    "PALPITE",
    "EXP:",
    "REF:",
    "COD BULTO",
    "BULTO",
    "PESO",
    "DATA",
    "FECHA",
    "REMITENTE",
    "AMAZON",
    "SPAIN",
    "MADRID",
    "TIPO PORTES",
    "PAGADO",
    "REEMBOLSO",
    "ENVIO",
    "1DE1",
    "1 DE1",
    "KGS",
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

    bloqueios_fortes = [
        "HTTP",
        "APP.COM",
        "WWW",
        "ATT:",
        "OBS",
        "PROCURAR",
        "PALPITE",
        "000030038",
        "EXP:",
        "REF:",
        "BULTO",
        "PESO",
        "COD.",
        "COD ",
        "GR:",
        "NºVEND",
        "VEND:",
        "DATA:",
        "HORA:",
    ]

    return any(x in l for x in bloqueios_fortes)


def extrair_cp_mesma_linha(linha: str) -> dict | None:
    original = str(linha).upper()

    if linha_tem_lixo_para_cp(original):
        return None

    linha_num = converter_ocr_numero(original)

    linha_num = linha_num.replace("PT-", "")
    linha_num = linha_num.replace("P-", "")
    linha_num = linha_num.replace("P ", "")

    # Normal: 3800-974, 3800 974, 3810-856
    m = re.search(r"\b(3800|3810)\s*[- ]\s*(\d{3})\b", linha_num)

    if m:
        cp = f"{m.group(1)}-{m.group(2)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)

            return {
                "codigo": cp,
                "cidade": cidade,
                "origem": "mesma_linha"
            }

    # OCR comum: P-3801-856AVRO => 3810-856 AVEIRO
    m_3801 = re.search(r"\b3801\s*[- ]\s*(\d{3})\b", linha_num)

    if m_3801 and (
        "AVEIRO" in original
        or "AVRO" in original
        or "AVR0" in original
        or "AVEI" in original
    ):
        cp = f"3810-{m_3801.group(1)}"

        if codigo_postal_valido(cp):
            cidade = linha_num[m_3801.end():].strip()
            cidade = corrigir_ocr_para_morada(cidade)

            return {
                "codigo": cp,
                "cidade": cidade,
                "origem": "corrigido_3801"
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

    prefixo = linha_limpa

    if prefixo == "3801":
        prefixo = "3810"

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

    return {
        "codigo": cp,
        "linha_index": i,
        "cidade": cidade,
        "origem": "partido"
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
                "origem": mesmo.get("origem", "mesma_linha")
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

    distancia = abs(cp_index - index)
    score += max(0, 70 - distancia * 10)

    if index < cp_index:
        score += 30

    if index > cp_index:
        score -= 20

    if index < cp_index - 8:
        score -= 35

    return score


def encontrar_morada_para_codigo(linhas: list[str], cp_info: dict) -> str:
    cp_index = cp_info["linha_index"]

    candidatos = []

    inicio = max(0, cp_index - 12)
    fim = min(len(linhas), cp_index + 3)

    for i in range(inicio, fim):
        linha = limpar_linha(corrigir_ocr_para_morada(linhas[i]))

        if eh_morada_valida(linha):
            candidatos.append({
                "linha": linha,
                "score": pontuar_morada(linha, i, cp_index),
                "index": i
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

    if codigo_postal_valido(codigo):
        score += 200

    if morada and morada != "Não encontrada":
        score += 150

    if eh_morada_valida(morada):
        score += 70

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

        cidade = cp.get("cidade", "")
        cidade = corrigir_ocr_para_morada(cidade)

        idx = cp["linha_index"]

        contexto_inicio = max(0, idx - 5)
        contexto_fim = min(len(linhas), idx + 5)
        contexto = "\n".join(linhas[contexto_inicio:contexto_fim])

        item = {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": cp["codigo"],
            "cidade": cidade if cidade else "Não encontrada",
            "linha_codigo_index": idx,
            "origem_codigo": cp.get("origem", ""),
            "contexto": contexto
        }

        item["score"] = pontuar_resultado(item)

        resultados.append(item)

    resultados.sort(key=lambda x: x["score"], reverse=True)

    escolhido = escolher_destinatario(resultados)

    if escolhido:
        return {
            "morada": escolhido["morada"],
            "codigo_postal": escolhido["codigo_postal"],
            "cidade": escolhido["cidade"],
            "todos_resultados": resultados
        }

    return {
        "morada": "Não encontrada",
        "codigo_postal": "Não encontrado",
        "cidade": "Não encontrada",
        "todos_resultados": resultados
    }


# =========================
# IMAGEM
# =========================

def criar_versoes_imagem(caminho: str) -> list[str]:
    img = cv2.imread(caminho)

    if img is None:
        return [caminho]

    versoes = [caminho]
    base = str(uuid.uuid4())

    # Versão cinza
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray)
    versoes.append(gray_path)

    # Versão com nitidez
    blur = cv2.GaussianBlur(img, (0, 0), 3)
    sharp = cv2.addWeighted(img, 1.5, blur, -0.5, 0)
    sharp_path = f"uploads/sharp_{base}.jpg"
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
                        if isinstance(linha, list) or isinstance(linha, tuple):
                            if len(linha) >= 2:
                                data = linha[1]

                                if isinstance(data, tuple) or isinstance(data, list):
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


def rodar_ocr_em_versoes(versoes: list[str]) -> str:
    engine = get_ocr()
    textos = []

    for path in versoes:
        try:
            print(f"Tentando OCR em: {path}", flush=True)

            resultado = engine.ocr(path, cls=True)

            texto = extrair_texto_resultado_ocr(resultado)
            texto = normalizar_texto(texto)

            if texto:
                print("Texto encontrado nesta versão:", flush=True)
                print(texto, flush=True)
                textos.append(texto)

        except TypeError:
            try:
                resultado = engine.ocr(path)
                texto = extrair_texto_resultado_ocr(resultado)
                texto = normalizar_texto(texto)

                if texto:
                    textos.append(texto)
            except Exception:
                print("Erro ao tentar OCR nessa versão", flush=True)
                traceback.print_exc()

        except Exception:
            print("Erro ao tentar OCR nessa versão", flush=True)
            traceback.print_exc()

    return juntar_textos_unicos(textos)


# =========================
# EXPORTAÇÃO
# =========================

def gerar_dataframe_lote():
    return pd.DataFrame(lote_confirmado, columns=[
        "Morada",
        "Código Postal",
        "Cidade",
        "Texto OCR"
    ])


def salvar_lote_em_arquivos():
    df = gerar_dataframe_lote()

    df.to_excel(EXPORT_EXCEL, index=False)
    df.to_csv(EXPORT_CSV, index=False)


# =========================
# UPLOAD
# =========================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    caminhos_temporarios = []

    try:
        print("\n========== RECEBEU UPLOAD ==========", flush=True)
        print(f"Arquivo: {file.filename}", flush=True)
        print(f"Tipo: {file.content_type}", flush=True)

        upload_id = str(uuid.uuid4())

        nome = f"{upload_id}.jpg"
        caminho = f"uploads/{nome}"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        caminhos_temporarios.append(caminho)

        img = cv2.imread(caminho)

        if img is None:
            return {
                "erro": "Erro ao abrir imagem"
            }

        print("Imagem aberta com sucesso", flush=True)

        print("Criando versões da imagem para OCR...", flush=True)
        versoes = criar_versoes_imagem(caminho)
        caminhos_temporarios.extend(versoes)

        print("Iniciando OCR...", flush=True)
        texto = rodar_ocr_em_versoes(versoes)

        print("OCR finalizado", flush=True)
        print("\n========== TEXTO OCR ==========", flush=True)
        print(texto, flush=True)

        dados_extraidos = extrair_dados_aveiro(texto)

        morada = dados_extraidos["morada"]
        codigo = dados_extraidos["codigo_postal"]
        cidade = dados_extraidos["cidade"]

        uploads_pendentes[upload_id] = {
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "texto_ocr": texto,
            "todos_resultados": dados_extraidos["todos_resultados"]
        }

        print("\n========== SUGESTÃO EXTRAÍDA ==========", flush=True)
        print(f"Upload ID: {upload_id}", flush=True)
        print(f"Morada: {morada}", flush=True)
        print(f"Código Postal: {codigo}", flush=True)
        print(f"Cidade: {cidade}", flush=True)

        return {
            "status": "aguardando_confirmacao",
            "mensagem": "Confirme ou edite os dados antes de adicionar ao lote.",
            "upload_id": upload_id,
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "texto_ocr": texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados_extraidos["todos_resultados"],
            "total_lote": len(lote_confirmado),
            "filtro": "Somente códigos postais 3800 e 3810"
        }

    except Exception as e:
        print("\n========== ERRO ==========", flush=True)
        traceback.print_exc()

        return {
            "erro": str(e)
        }

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
        morada = limpar_linha(corrigir_ocr_para_morada(payload.morada))
        codigo = payload.codigo_postal.strip()
        cidade = limpar_linha(corrigir_ocr_para_morada(payload.cidade or ""))
        texto_ocr = payload.texto_ocr or ""

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            pendente = uploads_pendentes[payload.upload_id]

            if not texto_ocr:
                texto_ocr = pendente.get("texto_ocr", "")

        if not codigo_postal_valido(codigo):
            return {
                "erro": "Código postal inválido. Este sistema só aceita códigos postais de Aveiro começados por 3800 ou 3810."
            }

        if not morada or morada == "Não encontrada":
            return {
                "erro": "Morada vazia. Confirme ou escreva a morada correta."
            }

        item = {
            "Morada": morada,
            "Código Postal": codigo,
            "Cidade": cidade,
            "Texto OCR": texto_ocr
        }

        lote_confirmado.append(item)
        salvar_lote_em_arquivos()

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            del uploads_pendentes[payload.upload_id]

        return {
            "status": "adicionado_ao_lote",
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "total_lote": len(lote_confirmado)
        }

    except Exception as e:
        print("\n========== ERRO AO CONFIRMAR ==========", flush=True)
        traceback.print_exc()

        return {
            "erro": str(e)
        }


# =========================
# LOTE
# =========================

@app.get("/resumo-lote")
async def resumo_lote():
    return {
        "total": len(lote_confirmado),
        "itens": lote_confirmado
    }


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

    return {
        "status": "lote_limpo",
        "total": 0
    }


# =========================
# DOWNLOAD EXCEL
# =========================

@app.get("/download-excel")
async def download_excel():

    if not os.path.exists(EXPORT_EXCEL):
        salvar_lote_em_arquivos()

    return FileResponse(
        path=EXPORT_EXCEL,
        filename="resultado.xlsx"
    )


# =========================
# DOWNLOAD CSV
# =========================

@app.get("/download-csv")
async def download_csv():

    if not os.path.exists(EXPORT_CSV):
        salvar_lote_em_arquivos()

    return FileResponse(
        path=EXPORT_CSV,
        filename="resultado.csv"
    )