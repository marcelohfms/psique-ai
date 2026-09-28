# app/booking_texts.py
"""Frases de confirmação de agendamento enviadas ao paciente.

Fonte única usada pelo fast-path do grafo (app/graph/nodes.py) e pelo painel da
atendente (app/panel_booking.py). Mudar uma frase aqui muda nos dois lugares.
"""
from datetime import datetime

from app.graph.prompts import CORRECT_PIX_KEY

DOCTOR_LABELS = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}
_WEEKDAYS = {
    0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
    3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo",
}


def format_appt_line(doctor: str, start: datetime, session_note: str = "") -> str:
    """'Dr. Júlio — segunda-feira, 05/10/2026 às 14:00 (nota)'. `start` já em Recife."""
    label = DOCTOR_LABELS.get(doctor, doctor)
    when = f"{_WEEKDAYS[start.weekday()]}, {start.strftime('%d/%m/%Y às %H:%M')}"
    note = f" ({session_note})" if session_note else ""
    return f"{label} — {when}{note}"


def confirmation_text(kind: str, appt_line: str, contact_name: str) -> str:
    """kind: normal | taxa_isenta | cortesia | taxa_paga."""
    if kind == "cortesia":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"Como combinado, a taxa de reserva está isenta. Até lá!"
        )
    if kind == "taxa_isenta":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"A taxa de reserva foi dispensada. Até lá!"
        )
    if kind == "taxa_paga":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"A taxa de reserva já está paga. Até lá!"
        )
    if kind == "normal":
        return (
            f"Consulta registrada! ✅\n{appt_line}\n\n"
            f"Para garantir a vaga, é necessário o pagamento da taxa de reserva de R$ 100,00 em até 2 horas.\n"
            f"💳 PIX: {CORRECT_PIX_KEY}\n\n"
            f"Esse valor será abatido do total da consulta. Em caso de cancelamento ou remarcação com menos de 24h de antecedência ou ausência sem justificativa, a taxa não é devolvida."
        )
    raise ValueError(f"kind desconhecido: {kind}")


def _hello(contact_name: str) -> str:
    return f"Olá, {contact_name}!" if contact_name else "Olá!"


def change_text(initiated_by: str, contact_name: str, old_line: str, new_line: str,
                new_fee: bool = False) -> str:
    """Aviso de consulta alterada pelo painel. initiated_by: patient | clinic."""
    opening = ("A Clínica Psiquê precisou alterar sua consulta." if initiated_by == "clinic"
               else "Conforme combinado, sua consulta foi alterada.")
    txt = f"{_hello(contact_name)} {opening}\n\nEra: {old_line}\nAgora: {new_line}"
    if new_fee:
        return txt + (
            "\n\nComo a mudança foi pedida com menos de 24h de antecedência, a taxa de reserva "
            "anterior não é reaproveitada. Para garantir a nova data, é necessário o pagamento de "
            f"uma nova taxa de R$ 100,00 em até 2 horas.\n💳 PIX: {CORRECT_PIX_KEY}"
        )
    return txt + "\n\nQualquer dúvida, é só responder aqui."


_CANCEL_FEE_LINES = {
    "devolver": "A equipe vai providenciar a devolução da taxa de reserva.",
    "credito": "O valor da taxa de reserva fica guardado para a próxima consulta. "
               "Quando quiser remarcar, é só responder aqui.",
}


def cancel_text(initiated_by: str, contact_name: str, lines: list[str], fee_action: str | None) -> str:
    """Aviso de consulta cancelada pelo painel. fee_action: devolver | credito | reter | None."""
    plural = len(lines) > 1
    if initiated_by == "clinic":
        opening = "A Clínica Psiquê precisou cancelar " + ("suas consultas:" if plural else "sua consulta:")
    else:
        opening = "Conforme combinado, " + ("suas consultas foram canceladas:" if plural else "sua consulta foi cancelada:")
    txt = f"{_hello(contact_name)} {opening}\n" + "\n".join(lines)
    fee = _CANCEL_FEE_LINES.get(fee_action or "")
    return txt + (f"\n\n{fee}" if fee else "\n\nSe quiser marcar uma nova data, é só responder aqui.")
