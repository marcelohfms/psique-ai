"""Tests for app.auto_reply.is_auto_reply — detecção conservadora de respostas
automáticas de ausência do WhatsApp."""
import pytest

from app.auto_reply import is_auto_reply


# ── Positivos: assinaturas conhecidas de resposta automática ──────────────────

@pytest.mark.parametrize("text", [
    "Nao estou disponível nesse momento. Retornarei o contato assim que possivel.",
    "Não estou disponível nesse momento. Retornarei o contato assim que possível.",
    "NÃO ESTOU DISPONÍVEL NESSE MOMENTO. RETORNAREI O CONTATO ASSIM QUE POSSÍVEL.",
    "  não estou disponível nesse momento.   retornarei o contato assim que possível  ",
    "Não estou disponível no momento, retorno assim que puder.",
    "Não estou disponível agora. Assim que possível retornarei.",
])
def test_recognises_known_away_messages(text):
    assert is_auto_reply(text) is True


# ── Negativos: mensagens reais de paciente não podem ser silenciadas ──────────

@pytest.mark.parametrize("text", [
    "oi",
    "Quero marcar uma consulta com a Dra. Bruna",
    "não estou disponível na quarta, tem outro dia?",  # só um sinal, é pedido real
    "assim que possível me manda os horários",          # só um sinal, é pedido real
    "retornarei o contato da secretária que você pediu",  # sem "não estou disponível"
    "",
])
def test_does_not_flag_real_patient_messages(text):
    assert is_auto_reply(text) is False


# ── Resposta automática colada com pedido real → processa (não é só auto) ─────

def test_does_not_flag_when_real_content_is_appended():
    text = (
        "Não estou disponível nesse momento. Retornarei o contato assim que possível.\n"
        "Na verdade quero remarcar para sexta de manhã"
    )
    assert is_auto_reply(text) is False
