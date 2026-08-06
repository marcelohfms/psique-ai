import re as _re
import unicodedata as _ud

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

# Tokens que nunca aparecem num nome próprio brasileiro, mas são comuns quando a
# resposta de outra pergunta é capturada por engano no campo do nome. Basta um
# token (comparado por PALAVRA INTEIRA, com acento removido) para reprovar:
# "Eu não sou paciente Ainda" (resposta de "você é paciente?") virou user_name +
# guardian_name — Carlos Alberto, 5581982691700, 06/08/2026. A comparação é por
# palavra inteira de propósito: "Simone" não contém "sim", "Consuela" não contém
# "consulta". Um falso negativo (barrar nome real) só faz a Eva reperguntar o
# nome — barato; um falso positivo (aceitar lixo) vira cirurgia manual em banco
# + checkpoint — caro. Por isso o blocklist é enxuto e de altíssimo sinal.
_NON_NAME_TOKENS = {
    "eu", "sou", "nao", "sim", "ainda", "paciente", "consulta",
    "agendar", "marcar", "remarcar", "primeira", "retorno",
    "acompanhamento", "receita", "atestado", "quero", "gostaria",
    "preciso", "seria", "estou",
}

# Palavras gramaticais que não aparecem em nome de pessoa. Complementa
# _NON_NAME_TOKENS (que mira vocabulário de agendamento): aqui o alvo são as
# partículas de frase — artigos, pronomes, preposições, verbos auxiliares. As
# partículas que de fato aparecem em nome — de/do/da/dos/das/e, ver
# _LINKING_WORDS — ficam DE FORA desta lista de propósito: "Maria da Conceição
# dos Santos" é nome. Uma frase educada e bem formada passava por todas as
# outras camadas: nenhum dígito, uma vírgula só, menos de 80 caracteres e sem
# começar por artigo (caso Beatriz, 5587996089614: "Por gentileza, veja se ele
# consegue atender na quinta-feira" virou guardian_name).
_FUNCTION_WORDS = {
    "o", "a", "os", "as", "ao", "aos", "à", "às", "um", "uma", "uns", "umas",
    "em", "na", "no", "nas", "nos", "num", "numa", "pelo", "pela", "pelos", "pelas",
    "com", "sem", "por", "para", "pra", "pro", "até", "desde", "sobre",
    "que", "qual", "quais", "quando", "onde", "como", "porque", "pois",
    "mas", "ou", "se", "já", "ainda", "também", "só", "muito", "mais", "menos",
    "eu", "tu", "você", "voce", "vc", "ele", "ela", "eles", "elas", "nós", "vocês",
    "me", "te", "lhe", "meu", "minha", "meus", "minhas", "seu", "sua", "seus", "suas",
    "este", "esta", "esse", "essa", "isso", "isto", "aquele", "aquela", "aquilo",
    "é", "são", "foi", "seja", "ser", "está", "estar", "tem", "ter", "tenho",
    "pode", "poder", "posso", "quer", "quero", "vai", "vou", "ir", "fica", "ficar",
    "favor", "gentileza", "obrigado", "obrigada", "por favor",
}

_MAX_NAME_WORDS = 7


def _fold(s: str) -> str:
    """Lowercase + remove acentos, para comparar tokens sem depender de acento."""
    return "".join(
        c for c in _ud.normalize("NFD", s.lower()) if _ud.category(c) != "Mn"
    )


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
    # Uma resposta de conversa de uma linha só ("Eu não sou paciente ainda",
    # "Quero uma receita") não tem quebra de linha nem dígito, então escapava de
    # todas as guardas acima. Um único token de conversa denuncia a frase.
    if {_fold(w) for w in _re.findall(r"[^\W\d_]+", t)} & _NON_NAME_TOKENS:
        return False
    if not _re.search(r'[a-zA-ZÀ-ú]{2,}', t):
        return False
    # Não começa com artigo/possessivo genérico ("o paciente", "minha filha").
    if _re.match(r'^(o|a|os|as|meu|minha|seu|sua)\s', tl):
        return False
    # Uma palavra gramatical em qualquer posição denuncia uma frase. As partículas
    # legítimas de nome (de/do/da/dos/das/e) não estão em _FUNCTION_WORDS.
    _palavras = _re.findall(r"[0-9a-zA-ZÀ-ú'’-]+", tl)
    if any(p in _FUNCTION_WORDS for p in _palavras):
        return False
    # Teto de palavras: "Marcelo Rodrigues de Souza Brayner Filho" tem 6. Acima de
    # sete não é nome, é frase — e é o segundo sinal para as que escapam da lista.
    if len(_palavras) > _MAX_NAME_WORDS:
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
