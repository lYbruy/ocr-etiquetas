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

ARQUIVO_EXCEL = "exports/resultado.xlsx"
ARQUIVO_CSV = "exports/resultado.csv"

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
    "ELECTROMECANICOS",
    "ELECTROMECÂNICOS",
    "VIVEIRO",
    "VIVEIROS",
    "000030038",
    "SN1",
    "SNI",
    "R-",
    "PALPITE",
    "EXP:",
    "REF:",
    "COD BULTO",
    "COD. BULTO",
    "BULTO",
    "PESO",
    "DATA",
    "FECHA",
    "REMITENTE",
    "AMAZON",
    "SPAIN",
    "MADRID",
    "PAQ24",
    "REEMBOLSO",
    "TIPO PORTES",
    "PORTES",
    "PAGADO",
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


os.makedirs("uploads", exist_ok=True)
os.makedirs("exports", exist_ok=True)


ocr_engine = None
uploads_pendentes = {}


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
# OCR
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
# ROTAS TESTE
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

def limpar_linha(linha: str) -> str:
    linha = str(linha).strip()

    linha = linha.replace("º", "°")
    linha = linha.replace(" N ", " Nº ")
    linha = linha.replace(" N°", " Nº")
    linha = linha.replace(" N.", " Nº")
    linha = linha.replace(" No ", " Nº ")
    linha = linha.replace(" N0 ", " Nº ")
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
        "C4CIA": "CACIA",
        "CÁC1A": "CÁCIA",
        "CAC1A": "CACIA",
        "PORTUGA": "PORTUGAL",
        "POR TUGAL": "PORTUGAL",
        "A1AMEDA": "ALAMEDA",
        "A1ameda": "Alameda",
        "S1LVA": "SILVA",
        "Si1va": "Silva",
        "R0CHA": "ROCHA",
        "R0A": "RUA",
        "RU4": "RUA",
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

    texto = re.sub(r"\bN\s*(\d+)\b", r"Nº\1", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto)

    return limpar_linha(texto)


# =========================
# FILTROS
# =========================

def linha_tem_lixo(linha: str) -> bool:
    l = linha.upper()

    return any(x in l for x in PALAVRAS_DESCARTAR)


def cp_eh_aveiro(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", cp):
        return False

    return cp[:4] in PREFIXOS_ACEITES


def codigo_postal_valido_aveiro(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", cp):
        return False

    sufixo = int(cp[5:])

    if sufixo < 1:
        return False

    return cp_eh_aveiro(cp)


def texto_tem_localidade_aveiro(texto: str) -> bool:
    t = corrigir_ocr_para_morada(texto).upper()

    return any(loc in t for loc in LOCALIDADES_AVEIRO)


def converter_ocr_numero(texto: str) -> str:
    texto = str(texto).upper()

    texto = texto.replace("O", "0")
    texto = texto.replace("Q", "0")
    texto = texto.replace("I", "1")
    texto = texto.replace("L", "1")
    texto = texto.replace("|", "1")
    texto = texto.replace("S", "5")
    texto = texto.replace("B", "8")
    texto = texto.replace("G", "6")

    return texto


# =========================
# EXTRAIR CÓDIGO POSTAL
# =========================

def extrair_cp_mesma_linha(linha: str, index: int) -> dict | None:
    if linha_tem_lixo(linha):
        return None

    linha_num = converter_ocr_numero(linha)

    match = re.search(r"\b(3800|3810)\s*[- ]\s*(\d{3})\b", linha_num)

    if not match:
        return None

    cp = f"{match.group(1)}-{match.group(2)}"

    if not codigo_postal_valido_aveiro(cp):
        return None

    depois = linha_num[match.end():].strip()
    depois = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", depois)
    cidade = limpar_linha(corrigir_ocr_para_morada(depois))

    return {
        "codigo": cp,
        "linha_index": index,
        "cidade": cidade,
        "origem": "mesma_linha"
    }


def extrair_cp_partido(linhas: list[str], i: int) -> dict | None:
    linha = linhas[i].strip()

    if linha_tem_lixo(linha):
        return None

    linha_num = converter_ocr_numero(linha)
    apenas_numeros = re.sub(r"[^0-9]", "", linha_num)

    if apenas_numeros not in PREFIXOS_ACEITES:
        return None

    # procura o sufixo até 4 linhas abaixo
    # isto resolve casos tipo:
    # 3800
    # REF: alguma coisa
    # 974-AVEIRO
    limite = min(len(linhas), i + 5)

    for k in range(i + 1, limite):
        prox = linhas[k].strip()

        if not prox:
            continue

        if any(x in prox.upper() for x in ["REF:", "COD", "BULTO", "PESO", "EXP:"]):
            continue

        prox_num = converter_ocr_numero(prox)

        m = re.match(r"^\s*(\d{3})\s*[- ]?\s*(.*)$", prox_num)

        if not m:
            continue

        cp = f"{apenas_numeros}-{m.group(1)}"

        if not codigo_postal_valido_aveiro(cp):
            continue

        cidade = m.group(2).strip()
        cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
        cidade = limpar_linha(corrigir_ocr_para_morada(cidade))

        return {
            "codigo": cp,
            "linha_index": i,
            "cidade": cidade,
            "origem": "partido"
        }

    return None


def extrair_codigos_postais_aveiro(linhas: list[str]) -> list[dict]:
    encontrados = []

    for i, linha in enumerate(linhas):
        cp_mesma_linha = extrair_cp_mesma_linha(linha, i)

        if cp_mesma_linha:
            encontrados.append(cp_mesma_linha)
            continue

        cp_partido = extrair_cp_partido(linhas, i)

        if cp_partido:
            encontrados.append(cp_partido)

    unicos = []
    vistos = set()

    for item in encontrados:
        if item["codigo"] not in vistos:
            vistos.add(item["codigo"])
            unicos.append(item)

    return unicos


# =========================
# EXTRAIR MORADA
# =========================

def eh_linha_cidade(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper().strip()

    if not l:
        return False

    if linha_tem_lixo(l):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    letras = len(re.findall(r"[A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ]", l))
    numeros = len(re.findall(r"\d", l))

    return letras >= 3 and numeros <= 2


def eh_morada_valida(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper().strip()

    if not l:
        return False

    if linha_tem_lixo(l):
        return False

    tem_palavra_morada = any(p in l for p in PALAVRAS_MORADA)
    tem_numero = bool(re.search(r"\d", l))

    return tem_palavra_morada and tem_numero


def pontuar_morada(linha: str, index: int, cp_index: int) -> int:
    l = corrigir_ocr_para_morada(linha).upper()
    score = 0

    if eh_morada_valida(linha):
        score += 100

    if "AVENIDA" in l or "AV." in l:
        score += 25

    if "RUA" in l:
        score += 25

    if "ALAMEDA" in l:
        score += 20

    if "TRAVESSA" in l:
        score += 15

    if "ESTRADA" in l:
        score += 15

    if "Nº" in l or "N°" in l:
        score += 15

    if re.search(r"\d", l):
        score += 15

    distancia = abs(cp_index - index)
    score += max(0, 60 - distancia * 8)

    # normalmente a morada vem antes do código postal
    if index < cp_index:
        score += 20

    if index > cp_index:
        score -= 15

    # se estiver muito acima, pode ser remetente
    if index < cp_index - 9:
        score -= 30

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

    if codigo_postal_valido_aveiro(codigo):
        score += 150

    if morada and morada != "Não encontrada":
        score += 120

    if eh_morada_valida(morada):
        score += 70

    if cidade and cidade != "Não encontrada":
        score += 20

    if texto_tem_localidade_aveiro(cidade):
        score += 50

    if texto_tem_localidade_aveiro(contexto):
        score += 40

    score += int(resultado.get("linha_codigo_index", 0)) * 2

    contexto_upper = contexto.upper()

    if any(x in contexto_upper for x in ["R-", "SN1", "PALPITE", "ATT:", "OBS", "EXP:", "REF:", "BULTO"]):
        score -= 45

    return score


def escolher_melhor_resultado(resultados: list[dict]) -> dict | None:
    validos = [
        r for r in resultados
        if codigo_postal_valido_aveiro(r["codigo_postal"])
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
        morada = encontrar_morada_para_codigo(linhas, cp)

        cidade = cp.get("cidade", "")
        cidade = corrigir_ocr_para_morada(cidade)

        idx = cp["linha_index"]

        contexto_inicio = max(0, idx - 5)
        contexto_fim = min(len(linhas), idx + 6)
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

    escolhido = escolher_melhor_resultado(resultados)

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

    # Versão melhorada única para não ficar lento demais
    resized = cv2.resize(
        img,
        None,
        fx=1.35,
        fy=1.35,
        interpolation=cv2.INTER_CUBIC
    )

    blur = cv2.GaussianBlur(resized, (0, 0), 2)
    sharp = cv2.addWeighted(resized, 1.45, blur, -0.45, 0)

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

def garantir_arquivo_exportacao():
    if not os.path.exists(ARQUIVO_EXCEL):
        pd.DataFrame(columns=[
            "Morada",
            "Código Postal",
            "Cidade"
        ]).to_excel(ARQUIVO_EXCEL, index=False)

    if not os.path.exists(ARQUIVO_CSV):
        pd.DataFrame(columns=[
            "Morada",
            "Código Postal",
            "Cidade"
        ]).to_csv(ARQUIVO_CSV, index=False)


def salvar_resultado_confirmado(morada: str, codigo: str, cidade: str):
    garantir_arquivo_exportacao()

    novo = pd.DataFrame([{
        "Morada": morada,
        "Código Postal": codigo,
        "Cidade": cidade
    }])

    antigo = pd.read_excel(ARQUIVO_EXCEL)

    final = pd.concat([antigo, novo], ignore_index=True)

    final.to_excel(ARQUIVO_EXCEL, index=False)
    final.to_csv(ARQUIVO_CSV, index=False)


def contar_exportados() -> int:
    if not os.path.exists(ARQUIVO_EXCEL):
        return 0

    try:
        df = pd.read_excel(ARQUIVO_EXCEL)
        return len(df)
    except Exception:
        return 0


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
        caminho = f"uploads/{upload_id}.jpg"

        with open(caminho, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        caminhos_temporarios.append(caminho)

        img = cv2.imread(caminho)

        if img is None:
            return {
                "erro": "Erro ao abrir imagem"
            }

        print("Imagem aberta com sucesso", flush=True)

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
            "mensagem": "Confirme ou edite os dados antes de guardar no lote.",
            "upload_id": upload_id,
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "texto_ocr": texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados_extraidos["todos_resultados"],
            "total_exportado": contar_exportados(),
            "filtro": "Somente códigos 3800 e 3810 de Aveiro"
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
# CONFIRMAR / GUARDAR NO LOTE
# =========================

@app.post("/confirmar")
async def confirmar(payload: ConfirmarPayload):

    try:
        morada = limpar_linha(corrigir_ocr_para_morada(payload.morada))
        codigo = payload.codigo_postal.strip()
        cidade = limpar_linha(corrigir_ocr_para_morada(payload.cidade or ""))

        if not codigo_postal_valido_aveiro(codigo):
            return {
                "erro": "Código postal inválido. Este sistema só aceita códigos 3800 ou 3810 de Aveiro."
            }

        if not morada or morada == "Não encontrada":
            return {
                "erro": "Morada vazia. Confirme ou escreva a morada correta."
            }

        salvar_resultado_confirmado(
            morada=morada,
            codigo=codigo,
            cidade=cidade
        )

        if payload.upload_id and payload.upload_id in uploads_pendentes:
            del uploads_pendentes[payload.upload_id]

        return {
            "status": "guardado_no_lote",
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "total_exportado": contar_exportados()
        }

    except Exception as e:
        print("\n========== ERRO AO CONFIRMAR ==========", flush=True)
        traceback.print_exc()

        return {
            "erro": str(e)
        }


# =========================
# RESUMO / LIMPAR LOTE
# =========================

@app.get("/resumo-lote")
async def resumo_lote():
    return {
        "total": contar_exportados()
    }


@app.post("/limpar-lote")
async def limpar_lote():
    try:
        if os.path.exists(ARQUIVO_EXCEL):
            os.remove(ARQUIVO_EXCEL)

        if os.path.exists(ARQUIVO_CSV):
            os.remove(ARQUIVO_CSV)

        garantir_arquivo_exportacao()

        return {
            "status": "lote_limpo",
            "total": 0
        }

    except Exception as e:
        return {
            "erro": str(e)
        }


# =========================
# DOWNLOAD EXCEL / CSV
# =========================

@app.get("/download-excel")
async def download_excel():
    garantir_arquivo_exportacao()

    return FileResponse(
        path=ARQUIVO_EXCEL,
        filename="resultado.xlsx"
    )


@app.get("/download-csv")
async def download_csv():
    garantir_arquivo_exportacao()

    return FileResponse(
        path=ARQUIVO_CSV,
        filename="resultado.csv"
    )