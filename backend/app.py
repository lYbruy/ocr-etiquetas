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
    "WWW",
    "APP.COM",
    "ATT:",
    "OBS",
    "PROCURAR",
    "EXP:",
    "REF:",
    "BULTO",
    "PESO",
    "DATA",
    "FECHA",
    "REEMBOLSO",
    "PAGADO",
    "TIPO PORTES",
    "COD. BULTO",
    "COD BULTO",
    "PAQ24",
    "SN1",
    "SNI",
    "R-",
    "PALPITE",
    "REMITENTE",
    "AMAZON",
    "SPAIN",
    "MADRID",
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
# PASTAS / ESTADO
# =========================

os.makedirs("uploads", exist_ok=True)
os.makedirs("exports", exist_ok=True)

ocr_engine = None
uploads_pendentes = {}
lote_confirmado = []


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
# LIMPAR LOTE
# =========================

@app.post("/limpar-lote")
async def limpar_lote():
    global lote_confirmado
    global uploads_pendentes

    lote_confirmado = []
    uploads_pendentes = {}

    for arquivo in [ARQUIVO_EXCEL, ARQUIVO_CSV]:
        try:
            if os.path.exists(arquivo):
                os.remove(arquivo)
        except Exception:
            pass

    return {
        "status": "limpo",
        "total": 0
    }


@app.get("/resumo-lote")
async def resumo_lote():
    return {
        "total": len(lote_confirmado),
        "itens": lote_confirmado
    }


# =========================
# NORMALIZAÇÃO
# =========================

def limpar_linha(linha: str) -> str:
    linha = str(linha).strip()

    linha = linha.replace("º", "°")
    linha = linha.replace("N°", "Nº")
    linha = linha.replace("N.", "Nº")
    linha = linha.replace(" N ", " Nº ")
    linha = linha.replace(" N°", " Nº")
    linha = linha.replace(" N.", " Nº")
    linha = linha.replace(" No ", " Nº ")
    linha = linha.replace(" N0 ", " Nº ")

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
        "CAC1A": "CACIA",
        "CÁC1A": "CÁCIA",
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
        "AVENIDA EUROPA Nº 292": "AVENIDA EUROPA Nº292",
    }

    for errado, certo in trocas.items():
        texto = texto.replace(errado, certo)

    texto = re.sub(
        r"\b(AVENIDA|RUA|ALAMEDA|TRAVESSA|ESTRADA|CAMINHO|LARGO|PRAÇA|PRACA)\s*([A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]+?)\s+N[º°]?\s*(\d+)",
        r"\1 \2 Nº\3",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"\b(AVENIDA|RUA|ALAMEDA|TRAVESSA|ESTRADA|CAMINHO|LARGO|PRAÇA|PRACA)([A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ])",
        r"\1 \2",
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
# VALIDADORES
# =========================

def linha_tem_lixo(linha: str) -> bool:
    l = linha.upper()
    return any(p in l for p in PALAVRAS_DESCARTAR)


def codigo_postal_aveiro_valido(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", cp):
        return False

    prefixo = cp[:4]
    sufixo = int(cp[5:])

    if prefixo not in PREFIXOS_ACEITES:
        return False

    if sufixo < 1:
        return False

    return True


def eh_morada_valida(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper()

    if not l:
        return False

    if linha_tem_lixo(l):
        return False

    tem_palavra = any(p in l for p in PALAVRAS_MORADA)
    tem_numero = bool(re.search(r"\d", l))

    return tem_palavra and tem_numero


def eh_linha_cidade(linha: str) -> bool:
    l = corrigir_ocr_para_morada(linha).upper()

    if not l:
        return False

    if linha_tem_lixo(l):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    letras = len(re.findall(r"[A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ]", l))
    numeros = len(re.findall(r"\d", l))

    return letras >= 3 and numeros <= 2


# =========================
# EXTRAÇÃO DO CÓDIGO POSTAL
# =========================

def extrair_codigos_postais_aveiro(linhas: list[str]) -> list[dict]:
    encontrados = []

    for i, linha in enumerate(linhas):
        linha_original = linha.strip()

        if linha_tem_lixo(linha_original):
            continue

        linha_num = converter_ocr_numero(linha_original)

        # Caso 1: 3800-974 / 3800 974 / 3810-193
        matches = re.finditer(r"\b(3800|3810)\s*[- ]\s*(\d{3})\b", linha_num)

        for m in matches:
            cp = f"{m.group(1)}-{m.group(2)}"

            if codigo_postal_aveiro_valido(cp):
                cidade = ""

                depois = linha_original[m.end():].strip(" -,.")
                if depois:
                    cidade = corrigir_ocr_para_morada(depois)

                if not cidade and i + 1 < len(linhas) and eh_linha_cidade(linhas[i + 1]):
                    cidade = corrigir_ocr_para_morada(linhas[i + 1])

                encontrados.append({
                    "codigo": cp,
                    "linha_index": i,
                    "cidade": cidade,
                    "origem": "mesma_linha"
                })

        # Caso 2: linha atual só tem 3800/3810 e a próxima tem 974-AVEIRO
        somente_numeros = re.sub(r"[^0-9]", "", linha_num)

        if somente_numeros in PREFIXOS_ACEITES and i + 1 < len(linhas):
            prox_original = linhas[i + 1].strip()

            if linha_tem_lixo(prox_original):
                continue

            prox_num = converter_ocr_numero(prox_original)
            m2 = re.match(r"^\s*(\d{3})\s*[- ]?\s*(.*)$", prox_num)

            if m2:
                cp = f"{somente_numeros}-{m2.group(1)}"

                if codigo_postal_aveiro_valido(cp):
                    cidade = m2.group(2).strip()
                    cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
                    cidade = limpar_linha(corrigir_ocr_para_morada(cidade))

                    encontrados.append({
                        "codigo": cp,
                        "linha_index": i,
                        "cidade": cidade,
                        "origem": "partido"
                    })

    unicos = []
    vistos = set()

    for item in encontrados:
        chave = f"{item['codigo']}-{item['linha_index']}"

        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)

    return unicos


# =========================
# EXTRAÇÃO DA MORADA
# =========================

def pontuar_morada(linha: str, index: int, cp_index: int) -> int:
    l = corrigir_ocr_para_morada(linha).upper()
    score = 0

    if eh_morada_valida(linha):
        score += 120

    if "RUA" in l:
        score += 25

    if "AVENIDA" in l or "AV." in l:
        score += 25

    if "ALAMEDA" in l:
        score += 20

    if "TRAVESSA" in l:
        score += 15

    if "ESTRADA" in l or "CAMINHO" in l:
        score += 15

    if "Nº" in l or "N°" in l:
        score += 15

    if re.search(r"\d", l):
        score += 15

    distancia = abs(cp_index - index)
    score += max(0, 70 - distancia * 10)

    # Normalmente a morada vem antes do código postal
    if index < cp_index:
        score += 25

    # Mas se estiver muito longe, perde força
    if index < cp_index - 8:
        score -= 40

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


def pontuar_resultado(item: dict) -> int:
    score = 0

    morada = item.get("morada", "")
    cp = item.get("codigo_postal", "")
    cidade = item.get("cidade", "")
    contexto = item.get("contexto", "")

    if codigo_postal_aveiro_valido(cp):
        score += 200

    if morada and morada != "Não encontrada":
        score += 120

    if eh_morada_valida(morada):
        score += 80

    if cidade and cidade != "Não encontrada":
        score += 20

    if "AVEIRO" in corrigir_ocr_para_morada(contexto).upper():
        score += 40

    if "CACIA" in corrigir_ocr_para_morada(contexto).upper() or "CÁCIA" in corrigir_ocr_para_morada(contexto).upper():
        score += 40

    # Penaliza linhas de referência/logística
    contexto_upper = contexto.upper()
    if any(x in contexto_upper for x in ["R-", "SN1", "PALPITE", "ATT:", "OBS", "EXP:", "REF:", "BULTO"]):
        score -= 80

    # Se está mais abaixo na etiqueta, costuma ser destinatário
    score += int(item.get("linha_codigo_index", 0)) * 3

    return score


def extrair_dados_aveiro(texto: str) -> dict:
    texto = normalizar_texto(texto)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]

    cps = extrair_codigos_postais_aveiro(linhas)

    resultados = []

    for cp in cps:
        codigo = cp["codigo"]

        if not codigo_postal_aveiro_valido(codigo):
            continue

        idx = cp["linha_index"]

        contexto_inicio = max(0, idx - 6)
        contexto_fim = min(len(linhas), idx + 6)
        contexto = "\n".join(linhas[contexto_inicio:contexto_fim])

        morada = encontrar_morada_para_codigo(linhas, cp)

        cidade = limpar_linha(corrigir_ocr_para_morada(cp.get("cidade", "")))

        item = {
            "morada": morada if morada else "Não encontrada",
            "codigo_postal": codigo,
            "cidade": cidade if cidade else "Não encontrada",
            "linha_codigo_index": idx,
            "origem_codigo": cp.get("origem", ""),
            "contexto": contexto
        }

        item["score"] = pontuar_resultado(item)

        resultados.append(item)

    resultados.sort(key=lambda x: x["score"], reverse=True)

    if resultados:
        escolhido = resultados[0]

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
        "todos_resultados": []
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

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray)
    versoes.append(gray_path)

    resized = cv2.resize(
        img,
        None,
        fx=1.4,
        fy=1.4,
        interpolation=cv2.INTER_CUBIC
    )
    resized_path = f"uploads/resized_{base}.jpg"
    cv2.imwrite(resized_path, resized)
    versoes.append(resized_path)

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
                traceback.print_exc()

        except Exception:
            print("Erro ao tentar OCR nessa versão", flush=True)
            traceback.print_exc()

    return juntar_textos_unicos(textos)


# =========================
# EXPORTAÇÃO
# =========================

def gerar_arquivos_lote():
    df = pd.DataFrame(
        lote_confirmado,
        columns=[
            "Morada",
            "Código Postal",
            "Cidade",
            "Texto OCR"
        ]
    )

    df.to_excel(ARQUIVO_EXCEL, index=False)
    df.to_csv(ARQUIVO_CSV, index=False)


def salvar_resultado_confirmado(morada: str, codigo: str, cidade: str, texto_ocr: str):
    lote_confirmado.append({
        "Morada": morada,
        "Código Postal": codigo,
        "Cidade": cidade,
        "Texto OCR": texto_ocr
    })

    gerar_arquivos_lote()


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

        dados = extrair_dados_aveiro(texto)

        uploads_pendentes[upload_id] = {
            "morada": dados["morada"],
            "codigo_postal": dados["codigo_postal"],
            "cidade": dados["cidade"],
            "texto_ocr": texto,
            "todos_resultados": dados["todos_resultados"]
        }

        print("\n========== SUGESTÃO EXTRAÍDA ==========", flush=True)
        print(f"Upload ID: {upload_id}", flush=True)
        print(f"Morada: {dados['morada']}", flush=True)
        print(f"Código Postal: {dados['codigo_postal']}", flush=True)
        print(f"Cidade: {dados['cidade']}", flush=True)

        return {
            "status": "aguardando_confirmacao",
            "mensagem": "Confirme ou edite os dados antes de adicionar ao lote.",
            "upload_id": upload_id,
            "morada": dados["morada"],
            "codigo_postal": dados["codigo_postal"],
            "cidade": dados["cidade"],
            "texto_ocr": texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados["todos_resultados"],
            "total_lote": len(lote_confirmado),
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
# CONFIRMAR
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

        if not codigo_postal_aveiro_valido(codigo):
            return {
                "erro": "Código postal inválido. Só são aceites códigos 3800 ou 3810."
            }

        if not morada or morada == "Não encontrada":
            return {
                "erro": "Morada vazia. Confirme ou escreva a morada correta."
            }

        salvar_resultado_confirmado(
            morada=morada,
            codigo=codigo,
            cidade=cidade,
            texto_ocr=texto_ocr
        )

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
# DOWNLOADS
# =========================

@app.get("/download-excel")
async def download_excel():
    if not os.path.exists(ARQUIVO_EXCEL):
        gerar_arquivos_lote()

    return FileResponse(
        path=ARQUIVO_EXCEL,
        filename="resultado.xlsx"
    )


@app.get("/download-csv")
async def download_csv():
    if not os.path.exists(ARQUIVO_CSV):
        gerar_arquivos_lote()

    return FileResponse(
        path=ARQUIVO_CSV,
        filename="resultado.csv"
    )