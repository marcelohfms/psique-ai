"""Custom attributes do paciente para a lateral da conversa no Chatwoot.

Lógica pura (sem I/O). Os valores descrevem sempre o paciente da consulta mais
próxima do telefone. Gravados como custom attributes de CONTATO por um cron
(scripts/sync_contact_attributes.py). As attribute_key abaixo precisam existir
no painel do Chatwoot (Administração > Atributos personalizados, tipo contato).
"""
from datetime import datetime

from app.scheduling_stall import parse_ts

# attribute_key (usadas na API do Chatwoot e no cadastro do painel).
ATTR_MEDICO = "medico"
ATTR_PROXIMA = "proxima_consulta"
ATTR_TAXA = "taxa_reserva"
ATTR_RETORNANTE = "retornante"
ATTR_KEYS = (ATTR_MEDICO, ATTR_PROXIMA, ATTR_TAXA, ATTR_RETORNANTE)

# Evento phone-keyed (tabela events) que guarda o último dict gravado, para o
# cron só chamar o Chatwoot quando algum valor muda.
ATTR_EVENT = "contact_attributes_synced"


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def pick_next_appointment(appts: list[dict], now: datetime) -> dict | None:
    """A consulta agendada futura mais próxima, ou None. Ignora não-scheduled e
    passadas."""
    future = [
        a for a in appts
        if a.get("status") == "scheduled" and parse_ts(a["start_time"]) >= now
    ]
    if not future:
        return None
    return min(future, key=lambda a: parse_ts(a["start_time"]))


def fee_status(next_appt: dict, custom_price) -> str:
    """Paga / Pendente / Isenta para a taxa de reserva da próxima consulta."""
    if next_appt.get("booking_fee_waived") or custom_price == 0:
        return "Isenta"
    if next_appt.get("booking_fee_paid_at"):
        return "Paga"
    return "Pendente"


def format_next_appointment(next_appt: dict, patient_name: str, tz) -> str:
    """'DD/MM/AAAA HH:MM <modalidade> — <primeiro nome>'."""
    dt = parse_ts(next_appt["start_time"]).astimezone(tz)
    quando = dt.strftime("%d/%m/%Y %H:%M")
    modalidade = next_appt.get("modality") or ""
    label = f"{quando} {modalidade}".strip()
    first = _first_name(patient_name)
    return f"{label} — {first}" if first else label


def build_attributes(*, doctor_label: str, next_appt: dict | None,
                     patient_name: str, is_returning, custom_price, tz) -> dict:
    """Monta o dict dos quatro custom attributes (todos as ATTR_KEYS)."""
    if next_appt:
        proxima = format_next_appointment(next_appt, patient_name, tz)
        taxa = fee_status(next_appt, custom_price)
    else:
        proxima = "sem consulta futura"
        taxa = ""
    return {
        ATTR_MEDICO: doctor_label or "",
        ATTR_PROXIMA: proxima,
        ATTR_TAXA: taxa,
        ATTR_RETORNANTE: "Retornante" if is_returning else "Primeira vez",
    }


def needs_attr_change(new: dict, last: dict | None) -> bool:
    """Só chama o Chatwoot quando algum valor mudou desde a última gravação."""
    return new != last
