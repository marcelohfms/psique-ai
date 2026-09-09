"""Detecção de respostas automáticas de ausência do WhatsApp.

Quando o celular do paciente responde a Eva com uma mensagem automática de
ausência (WhatsApp Business "mensagem de ausência"), a Eva não deve tratar isso
como um pedido real e responder. Caso Natalia/Leonardo Pimentel (5581996332827,
08/09/2026): o "retornarei o contato" foi lido como pedido de remarcação.

A detecção é DELIBERADAMENTE conservadora: casa apenas contra assinaturas
conhecidas, por igualdade da mensagem inteira normalizada. Assim nunca silencia
um paciente de verdade — nem quando ele escreve só um dos trechos ("não estou
disponível na quarta"), nem quando cola um pedido real depois da resposta
automática (aí a igualdade não bate e o fluxo segue normal).
"""
import unicodedata


def _normalize(text: str) -> str:
    """minúsculas, sem acento, pontuação vira espaço, espaços colapsados."""
    lowered = text.casefold()
    no_accents = "".join(
        c for c in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(c)
    )
    return " ".join(
        "".join(c if c.isalnum() else " " for c in no_accents).split()
    )


# Assinaturas conhecidas de resposta automática, já normalizadas.
# Adicionar aqui quando surgir outra mensagem automática recorrente.
_AWAY_SIGNATURES = {
    "nao estou disponivel nesse momento retornarei o contato assim que possivel",
    "nao estou disponivel no momento retorno assim que puder",
    "nao estou disponivel agora assim que possivel retornarei",
}


def is_auto_reply(text: str) -> bool:
    """True se `text` for, na íntegra, uma resposta automática de ausência conhecida."""
    if not text:
        return False
    return _normalize(text) in _AWAY_SIGNATURES
