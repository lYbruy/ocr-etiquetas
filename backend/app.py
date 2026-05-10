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
# OCR GLOBAL
# =========================

ocr_engine = None
uploads_pendentes = {}


def get_ocr():
    global ocr_engine

    if ocr_engine is None:
        print("Inicializando PaddleOCR...", flush=True)

        ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="en"
        )

        print("PaddleOCR inicializado", flush=True)

    return ocr_engine


# =========================
# MODELO CONFIRMAÇÃO
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
    trocas = {
        "AVEIR0": "AVEIRO",
        "AYEIR0": "AVEIRO",
        "AVElRO": "AVEIRO",
        "AVElR0": "AVEIRO",
        "PORTUGA": "PORTUGAL",
        "POR TUGAL": "PORTUGAL",
        "A1AMEDA": "ALAMEDA",
        "A1ameda": "Alameda",
        "S1LVA": "SILVA",
        "Si1va": "Silva",
        "R0CHA": "ROCHA",
        "R0A": "RUA",
        "EUROPAN": "EUROPA Nº",
        "EUROPA N": "EUROPA Nº",
        "AVENIDAEUROPA": "AVENIDA EUROPA",
        "AVENIDA EUROPA N292": "AVENIDA EUROPA Nº292",
        "AVENIDA EUROPA N°292": "AVENIDA EUROPA Nº292",
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
]


# =========================
# CÓDIGO POSTAL PORTUGUÊS
# =========================

def linha_tem_lixo_para_cp(linha: str) -> bool:
    l = linha.upper()

    bloqueios = [
        "HTTP",
        "APP.COM",
        "WWW",
        "ATT:",
        "OBS",
        "PROCURAR",
        "R-",
        "SN1",
        "SNI",
        "PALPITE",
        "000030038",
    ]

    return any(x in l for x in bloqueios)


def converter_ocr_numero(texto: str) -> str:
    texto = texto.upper()
    texto = texto.replace("O", "0")
    texto = texto.replace("I", "1")
    texto = texto.replace("L", "1")
    texto = texto.replace("S", "5")
    texto = texto.replace("B", "8")
    return texto


def codigo_postal_portugues_valido(cp: str) -> bool:
    if not re.match(r"^\d{4}-\d{3}$", cp):
        return False

    prefixo = int(cp[:4])
    sufixo = int(cp[5:])

    if prefixo < 1000:
        return False

    if sufixo < 1:
        return False

    return True


def extrair_cp_mesma_linha(linha: str) -> str:
    if linha_tem_lixo_para_cp(linha):
        return ""

    linha_num = converter_ocr_numero(linha)

    # Aceita 3800-385, 3800 - 385, 3800 385
    m = re.search(r"\b([1-9]\d{3})\s*[- ]\s*(\d{3})\b", linha_num)

    if m:
        cp = f"{m.group(1)}-{m.group(2)}"

        if codigo_postal_portugues_valido(cp):
            return cp

    return ""


def extrair_cp_partido(linhas: list[str], i: int) -> dict | None:
    linha = linhas[i].strip()

    if linha_tem_lixo_para_cp(linha):
        return None

    # Para evitar erro tipo R-1155, só aceita linha quase só com 4 dígitos.
    linha_num = converter_ocr_numero(linha)
    linha_limpa = re.sub(r"[^0-9]", "", linha_num)

    if not re.fullmatch(r"[1-9]\d{3}", linha_limpa):
        return None

    if i + 1 >= len(linhas):
        return None

    prox = linhas[i + 1].strip()

    if linha_tem_lixo_para_cp(prox):
        return None

    prox_num = converter_ocr_numero(prox)

    # Aceita: 385 AVEIRO, 385-AVEIRO, 974-AVEIRO
    m = re.match(r"^\s*(\d{3})\s*[- ]?\s*(.*)$", prox_num)

    if not m:
        return None

    cp = f"{linha_limpa}-{m.group(1)}"

    if not codigo_postal_portugues_valido(cp):
        return None

    cidade = m.group(2).strip()
    cidade = re.sub(r"[^A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ ]", " ", cidade)
    cidade = limpar_linha(corrigir_ocr_para_morada(cidade))

    return {
        "codigo": cp,
        "linha_index": i,
        "cidade": cidade,
        "origem": "partido"
    }


def extrair_codigos_postais_portugal(linhas: list[str]) -> list[dict]:
    encontrados = []

    for i, linha in enumerate(linhas):
        cp = extrair_cp_mesma_linha(linha)

        if cp:
            cidade = ""

            padrao = re.compile(r"[1-9]\d{3}\s*[- ]\s*\d{3}", re.IGNORECASE)
            partes = padrao.split(converter_ocr_numero(linha), maxsplit=1)

            if len(partes) > 1:
                cidade = partes[1].strip()

            if not cidade and i + 1 < len(linhas):
                prox = linhas[i + 1].strip()
                if eh_linha_cidade(prox):
                    cidade = prox

            encontrados.append({
                "codigo": cp,
                "linha_index": i,
                "cidade": limpar_linha(corrigir_ocr_para_morada(cidade)),
                "origem": "mesma_linha"
            })

            continue

        cp_partido = extrair_cp_partido(linhas, i)

        if cp_partido:
            encontrados.append(cp_partido)

    unicos = []
    vistos = set()

    for item in encontrados:
        chave = item["codigo"]

        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)

    return unicos


# =========================
# CIDADE / MORADA
# =========================

def eh_linha_cidade(linha: str) -> bool:
    l = linha.upper().strip()

    if not l:
        return False

    if any(x in l for x in PALAVRAS_DESCARTAR):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    letras = len(re.findall(r"[A-ZÁÀÂÃÉÈÊÍÌÓÒÔÕÚÙÇ]", l))
    numeros = len(re.findall(r"\d", l))

    return letras >= 3 and numeros <= 2


def eh_morada_valida(linha: str) -> bool:
    l = linha.upper().strip()

    if not l:
        return False

    if any(x in l for x in PALAVRAS_DESCARTAR):
        return False

    tem_palavra_morada = any(p in l for p in PALAVRAS_MORADA)
    tem_numero = bool(re.search(r"\d", l))

    return tem_palavra_morada and tem_numero


def pontuar_morada(linha: str, index: int, cp_index: int) -> int:
    l = linha.upper()
    score = 0

    if eh_morada_valida(linha):
        score += 80

    if "AVENIDA" in l or "AV." in l:
        score += 20

    if "RUA" in l:
        score += 20

    if "ALAMEDA" in l:
        score += 18

    if "TRAVESSA" in l:
        score += 15

    if "ESTRADA" in l:
        score += 15

    if "Nº" in linha or "N°" in linha:
        score += 12

    if re.search(r"\d", l):
        score += 10

    distancia = abs(cp_index - index)
    score += max(0, 40 - distancia * 7)

    # Morada normalmente vem antes do CP
    if index < cp_index:
        score += 10

    # Evita pegar remetente muito acima quando há destinatário mais perto
    if index < cp_index - 5:
        score -= 20

    return score


def encontrar_morada_para_codigo(linhas: list[str], cp_info: dict) -> str:
    cp_index = cp_info["linha_index"]

    candidatos = []

    inicio = max(0, cp_index - 8)
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

    if codigo_postal_portugues_valido(codigo):
        score += 80

    if morada and morada != "Não encontrada":
        score += 80

    if cidade and cidade != "Não encontrada":
        score += 20

    if eh_morada_valida(morada):
        score += 30

    # Quanto mais abaixo na etiqueta, mais provável ser destinatário
    score += int(resultado.get("linha_codigo_index", 0)) * 3

    # Penaliza CP suspeito perto de referências
    texto_contexto = resultado.get("contexto", "").upper()

    if any(x in texto_contexto for x in ["R-", "SN1", "PALPITE", "ATT:", "OBS"]):
        score -= 100

    return score


def escolher_destinatario(resultados: list[dict]) -> dict | None:
    validos = [
        r for r in resultados
        if codigo_postal_portugues_valido(r["codigo_postal"])
    ]

    if not validos:
        return None

    validos.sort(key=lambda x: x["score"], reverse=True)

    return validos[0]


def extrair_dados_portugal(texto: str) -> dict:
    texto = normalizar_texto(texto)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]

    cps = extrair_codigos_postais_portugal(linhas)

    resultados = []

    for cp in cps:
        if not codigo_postal_portugues_valido(cp["codigo"]):
            continue

        morada = encontrar_morada_para_codigo(linhas, cp)

        cidade = cp.get("cidade", "")
        cidade = corrigir_ocr_para_morada(cidade)

        idx = cp["linha_index"]

        contexto_inicio = max(0, idx - 3)
        contexto_fim = min(len(linhas), idx + 4)
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

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray)
    versoes.append(gray_path)

    resized = cv2.resize(
        img,
        None,
        fx=1.5,
        fy=1.5,
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

            resultado = engine.ocr(path)

            texto = extrair_texto_resultado_ocr(resultado)
            texto = normalizar_texto(texto)

            if texto:
                print("Texto encontrado nesta versão:", flush=True)
                print(texto, flush=True)
                textos.append(texto)

        except Exception:
            print("Erro ao tentar OCR nessa versão", flush=True)
            traceback.print_exc()

    return juntar_textos_unicos(textos)


# =========================
# EXPORTAÇÃO CONFIRMADA
# =========================

def salvar_resultado_confirmado(morada: str, codigo: str, cidade: str, texto_ocr: str):
    arquivo_excel = "exports/resultado.xlsx"
    arquivo_csv = "exports/resultado.csv"

    novo = pd.DataFrame([{
        "Morada": morada,
        "Código Postal": codigo,
        "Cidade": cidade,
        "Texto OCR": texto_ocr
    }])

    if os.path.exists(arquivo_excel):
        antigo = pd.read_excel(arquivo_excel)
        final = pd.concat([antigo, novo], ignore_index=True)
    else:
        final = novo

    final.to_excel(arquivo_excel, index=False)
    final.to_csv(arquivo_csv, index=False)


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

        print(f"Imagem salva em: {caminho}", flush=True)

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

        dados_extraidos = extrair_dados_portugal(texto)

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
            "mensagem": "Confirme ou edite os dados antes de exportar.",
            "upload_id": upload_id,
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade,
            "texto_ocr": texto if texto else "Nenhum texto encontrado",
            "todos_resultados": dados_extraidos["todos_resultados"]
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
# CONFIRMAR E EXPORTAR
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

        if not codigo_postal_portugues_valido(codigo):
            return {
                "erro": "Código postal português inválido. Use o formato 0000-000."
            }

        if not morada:
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
            "status": "exportado",
            "morada": morada,
            "codigo_postal": codigo,
            "cidade": cidade
        }

    except Exception as e:
        print("\n========== ERRO AO CONFIRMAR ==========", flush=True)
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
            "Cidade",
            "Texto OCR"
        ]).to_excel(arquivo_excel, index=False)

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
            "Cidade",
            "Texto OCR"
        ]).to_csv(arquivo_csv, index=False)

    return FileResponse(
        path=arquivo_csv,
        filename="resultado.csv"
    )