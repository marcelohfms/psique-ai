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
