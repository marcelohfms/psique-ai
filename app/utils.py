import re as _re

_COMPOUND_FIRST_NAMES = {"maria", "ana", "joão", "joao", "josé", "jose"}
_LINKING_WORDS = {"de", "do", "da", "dos", "das", "e"}

# Marcadores de anexo e de mídia. Quando o paciente responde a pergunta do nome
# com uma foto, um PDF ou um comprovante, é este texto que chega como resposta.
_MEDIA_MARKERS = (
    "[imagem]", "[pdf", "[áudio", "[audio", "[vídeo", "[video", "[documento",
    "[drive_link", "[sticker", "pdf-recebido",
    ".pdf", ".jpg", ".jpeg", ".png", ".mp3", ".ogg", ".mp4",
)

_NON_NAME_PHRASES = (
    "que coloquei", "o mesmo", "acima", "já disse", "ja disse",
    "o de cima", "igual", "conforme", "como disse", "como coloquei",
    "minha filha", "meu filho", "minha mae", "minha mãe", "meu pai",
    "o paciente", "a paciente",
)

_CONFIRMATIONS = {
    "sim", "não", "nao", "isso", "exato", "correto", "ok",
    "ele", "ela", "eu", "certo", "isso mesmo",
}


def looks_like_name(text: str) -> bool:
    """True quando o texto plausivelmente é o nome de uma pessoa.

    Guarda o campo do nome contra o corpo de uma mensagem qualquer. Quatro
    prontuários já ficaram com o nome trocado pelo conteúdo do que chegou logo
    depois da pergunta: um PDF de laudo, o texto de um comprovante de pagamento
    e a resposta da pergunta anterior ("Seria a primeira\\nConsulta").

    Deliberadamente permissiva. Um falso negativo faz a Eva reperguntar o nome
    para sempre — o modo de falha que este cadastro já teve com o parentesco
    (ver _normalize_relationship em app/graph/nodes.py). Por isso não há regra
    por contagem de palavras: "Marcelo Rodrigues de Souza Brayner Filho" tem
    seis, e é um nome legítimo.
    """
    t = (text or "").strip()
    if len(t) < 3:
        return False
    # Nome de pessoa não tem quebra de linha. Duas respostas coladas num mesmo
    # turno ("Seria a primeira\nConsulta") caem aqui.
    if "\n" in t or "\r" in t:
        return False
    if _re.search(r'\d', t):
        return False
    tl = t.lower()
    if any(m in tl for m in _MEDIA_MARKERS):
        return False
    # Mais de um sinal de pontuação denuncia uma frase inteira, não um nome.
    if sum(1 for c in t if c in '.!?,;:') > 1:
        return False
    if len(t) > 80:
        return False
    if any(p in tl for p in _NON_NAME_PHRASES):
        return False
    if tl in _CONFIRMATIONS:
        return False
    if not _re.search(r'[a-zA-ZÀ-ú]{2,}', t):
        return False
    # Não começa com artigo/possessivo genérico ("o paciente", "minha filha").
    if _re.match(r'^(o|a|os|as|meu|minha|seu|sua)\s', tl):
        return False
    return True


def display_name(full_name: str) -> str:
    """Return the name to use when addressing a contact/patient.

    For names starting with Maria, Ana, João or José, returns the first two
    words — or three if the second word is a linking word (de/do/da/dos/das/e),
    e.g. "Maria de Fátima" or "Maria do Carmo".
    For all other names, returns only the first word.
    """
    if not full_name:
        return full_name
    parts = full_name.split()
    if len(parts) >= 2 and parts[0].lower() in _COMPOUND_FIRST_NAMES:
        if len(parts) >= 3 and parts[1].lower() in _LINKING_WORDS:
            return f"{parts[0]} {parts[1]} {parts[2]}"
        return f"{parts[0]} {parts[1]}"
    return parts[0]
