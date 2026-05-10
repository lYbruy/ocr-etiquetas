from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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


def get_ocr():
    global ocr_engine

    if ocr_engine is None:
        print("Inicializando PaddleOCR...", flush=True)

        # Usa inglês porque reconhece bem caracteres latinos.
        # NÃO usar lang='latin' porque deu erro no teu Railway.
        ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="en"
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
    linha = linha.strip()

    linha = linha.replace("º", "°")
    linha = linha.replace(" N ", " Nº ")
    linha = linha.replace(" N°", " Nº")
    linha = linha.replace(" N.", " Nº")
    linha = linha.replace(" No ", " Nº ")

    linha = re.sub(r"\s+", " ", linha)

    return linha.strip()


def normalizar_texto(texto: str) -> str:
    texto = texto.replace("\r", "\n")
    texto = re.sub(r"\n+", "\n", texto)

    linhas = []

    for linha in texto.split("\n"):
        linha = limpar_linha(linha)
        if linha:
            linhas.append(linha)

    return "\n".join(linhas)


def corrigir_ocr_para_morada(texto: str) -> str:
    """
    Corrige só texto de morada/cidade.
    Não usar isto antes de extrair código postal,
    porque pode destruir números.
    """

    trocas = {
        "AVEIR0": "AVEIRO",
        "AYEIR0": "AVEIRO",
        "AVElRO": "AVEIRO",
        "PORTUGA": "PORTUGAL",
        "POR TUGAL": "PORTUGAL",
        "A1AMEDA": "ALAMEDA",
        "A1ameda": "Alameda",
        "S1LVA": "SILVA",
        "Si1va": "Silva",
        "R0CHA": "ROCHA",
        "R0A": "RUA",
    }

    for errado, certo in trocas.items():
        texto = texto.replace(errado, certo)

    return texto


# =========================
# CÓDIGO POSTAL PORTUGUÊS
# =========================

def normalizar_codigo_postal_linha(linha: str) -> str:
    """
    Tenta reconstruir códigos postais portugueses quando o OCR separa:
    3800
    385 AVEIRO

    ou quando lê:
    3800 385
    3800-385
    3800 - 385
    """

    linha_original = linha.upper()

    # Corrige letras comuns dentro de números
    linha_num = linha_original
    linha_num = linha_num.replace("O", "0")
    linha_num = linha_num.replace("I", "1")
    linha_num = linha_num.replace("L", "1")
    linha_num = linha_num.replace("S", "5")
    linha_num = linha_num.replace("B", "8")

    # padrão normal
    m = re.search(r"\b(\d{4})\s*[- ]\s*(\d{3})\b", linha_num)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    return ""


def extrair_codigos_postais_portugal(linhas: list[str]) -> list[dict]:
    encontrados = []

    for i, linha in enumerate(linhas):
        cp = normalizar_codigo_postal_linha(linha)

        if cp:
            cidade = ""

            # cidade pode estar na mesma linha depois do CP
            parte_depois = re.split(r"\d{4}\s*[- ]\s*\d{3}", linha, maxsplit=1)
            if len(parte_depois) > 1:
                cidade = parte_depois[1].strip()

            # ou na linha seguinte
            if not cidade and i + 1 < len(linhas):
                prox = linhas[i + 1].strip()
                if eh_linha_cidade(prox):
                    cidade = prox

            encontrados.append({
                "codigo": cp,
                "linha_index": i,
                "cidade": limpar_linha(corrigir_ocr_para_morada(cidade))
            })

        else:
            # caso partido:
            # linha atual: 3800
            # próxima linha: 385 AVEIRO ou 974-AVEIRO
            m1 = re.search(r"\b(\d{4})\b", linha)
            if m1 and i + 1 < len(linhas):
                prox = linhas[i + 1].upper()

                prox_num = prox
                prox_num = prox_num.replace("O", "0")
                prox_num = prox_num.replace("I", "1")
                prox_num = prox_num.replace("L", "1")
                prox_num = prox_num.replace("S", "5")
                prox_num = prox_num.replace("B", "8")

                m2 = re.search(r"\b(\d{3})\b", prox_num)

                if m2:
                    cp = f"{m1.group(1)}-{m2.group(1)}"

                    cidade = re.sub(r"\b\d{3}\b", "", prox).replace("-", " ").strip()
                    cidade = limpar_linha(corrigir_ocr_para_morada(cidade))

                    encontrados.append({
                        "codigo": cp,
                        "linha_index": i,
                        "cidade": cidade
                    })

    # remover duplicados
    unicos = []
    vistos = set()

    for item in encontrados:
        if item["codigo"] not in vistos:
            vistos.add(item["codigo"])
            unicos.append(item)

    return unicos


def codigo_postal_portugues_valido(cp: str) -> bool:
    """
    Formato português: 0000-000.
    Não garante que existe nos CTT, mas filtra lixo óbvio.
    """

    if not re.match(r"^\d{4}-\d{3}$", cp):
        return False

    prefixo = int(cp[:4])

    # Portugal continental e ilhas usam prefixos de 1000 a 9999.
    # Remove coisas tipo 0000-300.
    if prefixo < 1000:
        return False

    return True


# =========================
# MORADAS PORTUGUESAS
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
    "PORTUGAL",
    "YVES",
    "ROCHER",
    "COSMETICOS",
    "COSMÉTICOS",
    "LDA",
    "S.A",
    " SA ",
    "ELECTROMECANICOS",
    "VIVEIRO",
    "VIVEIROS",
    "000030038",
]


def eh_linha_cidade(linha: str) -> bool:
    l = linha.upper().strip()

    if not l:
        return False

    if any(x in l for x in PALAVRAS_DESCARTAR):
        return False

    if re.search(r"\d{4}-\d{3}", l):
        return False

    # cidades normalmente têm letras e poucos números
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
    """
    Quanto maior a pontuação, mais provável ser a morada do destinatário.
    """

    l = linha.upper()
    score = 0

    if eh_morada_valida(linha):
        score += 50

    if "RUA" in l:
        score += 12

    if "AVENIDA" in l or "AV." in l:
        score += 12

    if "ALAMEDA" in l:
        score += 12

    if "Nº" in linha or "N°" in linha or " N " in l:
        score += 8

    if re.search(r"\d", l):
        score += 8

    # Morada perto do código postal é mais provável
    distancia = abs(cp_index - index)
    score += max(0, 25 - distancia * 5)

    # Evitar remetente no topo quando existe destinatário mais abaixo
    if index < cp_index:
        score += 5

    return score


def encontrar_morada_para_codigo(linhas: list[str], cp_info: dict) -> str:
    cp_index = cp_info["linha_index"]

    candidatos = []

    # procurar até 6 linhas antes do código postal
    inicio = max(0, cp_index - 6)
    fim = min(len(linhas), cp_index + 2)

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


def escolher_destinatario(resultados: list[dict]) -> dict | None:
    """
    Se houver mais de uma morada/código, escolhe a mais provável do destinatário.
    Normalmente é a última morada portuguesa válida da etiqueta.
    """

    validos = [
        r for r in resultados
        if codigo_postal_portugues_valido(r["codigo_postal"])
    ]

    if not validos:
        return None

    # Preferir o último código postal válido, porque em etiquetas
    # o remetente costuma vir em cima e destinatário mais abaixo.
    validos.sort(key=lambda x: x["linha_codigo_index"], reverse=True)

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

        resultados.append({
            "morada": morada,
            "codigo_postal": cp["codigo"],
            "cidade": cidade,
            "linha_codigo_index": cp["linha_index"]
        })

    escolhido = escolher_destinatario(resultados)

    if escolhido:
        return {
            "morada": escolhido["morada"] if escolhido["morada"] else "Não encontrada",
            "codigo_postal": escolhido["codigo_postal"],
            "cidade": escolhido["cidade"] if escolhido["cidade"] else "Não encontrada",
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

    # Cinza
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_path = f"uploads/gray_{base}.jpg"
    cv2.imwrite(gray_path, gray)
    versoes.append(gray_path)

    # Redimensionada
    resized = cv2.resize(
        img,
        None,
        fx=1.7,
        fy=1.7,
        interpolation=cv2.INTER_CUBIC
    )
    resized_path = f"uploads/resized_{base}.jpg"
    cv2.imwrite(resized_path, resized)
    versoes.append(resized_path)

    # Nitidez
    blur = cv2.GaussianBlur(img, (0, 0), 3)
    sharp = cv2.addWeighted(img, 1.6, blur, -0.6, 0)
    sharp_path = f"uploads/sharp_{base}.jpg"
    cv2.imwrite(sharp_path, sharp)
    versoes.append(sharp_path)

    return versoes


# =========================
# PARSER DO RESULTADO OCR
# =========================

def extrair_texto_resultado_ocr(resultado) -> str:
    """
    Compatível com formatos diferentes do PaddleOCR.
    """

    textos = []

    if not resultado:
        return ""

    # PaddleOCR novo pode retornar lista de dicts
    if isinstance(resultado, list):
        for item in resultado:
            if isinstance(item, dict):
                if "rec_texts" in item and isinstance(item["rec_texts"], list):
                    textos.extend(item["rec_texts"])

                elif "text" in item:
                    textos.append(str(item["text"]))

            elif isinstance(item, list):
                for linha in item:
                    try:
                        # formato clássico:
                        # [box, ("texto", conf)]
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


def rodar_ocr_em_versoes(versoes: list[str]) -> str:
    engine = get_ocr()

    melhor_texto = ""

    for path in versoes:
        try:
            print(f"Tentando OCR em: {path}", flush=True)

            # NÃO usar cls=True, porque na tua versão deu erro.
            resultado = engine.ocr(path)

            texto = extrair_texto_resultado_ocr(resultado)
            texto = normalizar_texto(texto)

            if texto:
                print("Texto encontrado nesta versão:", flush=True)
                print(texto, flush=True)

            if len(texto) > len(melhor_texto):
                melhor_texto = texto

        except Exception:
            print("Erro ao tentar OCR nessa versão", flush=True)
            traceback.print_exc()

    return melhor_texto


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

        nome = f"{uuid.uuid4()}.jpg"
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

        print("\n========== DADOS EXTRAÍDOS ==========", flush=True)
        print(f"Morada: {morada}", flush=True)
        print(f"Código Postal: {codigo}", flush=True)
        print(f"Cidade: {cidade}", flush=True)

        arquivo_excel = "exports/resultado.xlsx"
        arquivo_csv = "exports/resultado.csv"

        novo = pd.DataFrame([{
            "Morada": morada,
            "Código Postal": codigo,
            "Cidade": cidade,
            "Texto OCR": texto
        }])

        if os.path.exists(arquivo_excel):
            antigo = pd.read_excel(arquivo_excel)
            final = pd.concat([antigo, novo], ignore_index=True)
        else:
            final = novo

        final.to_excel(arquivo_excel, index=False)
        final.to_csv(arquivo_csv, index=False)

        print("Arquivos Excel/CSV salvos com sucesso", flush=True)

        return {
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
        # Limpa versões temporárias para não encher o Railway
        for p in set(caminhos_temporarios):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


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