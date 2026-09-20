"""Sincroniza custom attributes de contato no Chatwoot (médico, próxima consulta,
taxa de reserva, retornante). Roda a cada 30 min via GitHub Actions.

- Candidatos: contatos com consulta futura agendada + os já sincronizados antes.
- Só chama o Chatwoot quando algum valor muda (memória via evento
  contact_attributes_synced) — protege do rate limit.
- Grava mesmo para contato pausado: custom attribute é info para a atendente, não
  mensagem ao paciente. Nada toca labels de controle nem o webhook/grafo.
"""
import asyncio
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.patient_attributes import (
    build_attributes, needs_attr_change, pick_next_appointment, ATTR_EVENT,
)
from app.database import (
    get_users_by_phone, get_user_by_phone, get_upcoming_appointments,
    log_event, get_events_by_type, DOCTOR_NAMES, DOCTOR_IDS,
)
from app.chatwoot import set_contact_custom_attributes, find_or_create_contact_id

TZ = ZoneInfo("America/Recife")

# Reexport para os testes referenciarem os ids reais por chave.
DOCTOR_IDS_BY_KEY = DOCTOR_IDS

DOCTOR_LABELS = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}


def _doctor_label(doctor_id) -> str:
    return DOCTOR_LABELS.get(DOCTOR_NAMES.get(doctor_id, ""), "")


async def candidate_phones(client, now: datetime) -> set[str]:
    """Telefones a sincronizar: quem tem consulta futura agendada + quem já foi
    sincronizado antes (para atualizar quando a consulta passa/cancela)."""
    now_iso = now.astimezone(timezone.utc).isoformat()
    appts = (
        await client.from_("appointments")
        .select("patient_id")
        .eq("status", "scheduled")
        .gte("start_time", now_iso)
        .execute()
    ).data or []
    patient_ids = list({a["patient_id"] for a in appts if a.get("patient_id")})

    phones: set[str] = set()
    if patient_ids:
        pcs = (
            await client.from_("patient_contacts")
            .select("contact_id")
            .in_("patient_id", patient_ids)
            .execute()
        ).data or []
        contact_ids = list({pc["contact_id"] for pc in pcs if pc.get("contact_id")})
        if contact_ids:
            contacts = (
                await client.from_("contacts")
                .select("phone")
                .in_("id", contact_ids)
                .execute()
            ).data or []
            phones = {c["phone"] for c in contacts if c.get("phone")}

    synced = (
        await client.from_("events")
        .select("phone")
        .eq("event_type", ATTR_EVENT)
        .execute()
    ).data or []
    phones |= {e["phone"] for e in synced if e.get("phone")}
    return phones


async def _build_attrs_for_phone(phone: str, now: datetime) -> dict:
    """Monta os quatro valores para o telefone, a partir do paciente da consulta
    mais próxima (ou do paciente mais recente quando não há consulta futura)."""
    users = await get_users_by_phone(phone) or []
    by_id = {u["id"]: u for u in users}
    appts = await get_upcoming_appointments(phone)
    next_appt = pick_next_appointment(appts, now)

    if next_appt and next_appt.get("patient_id") in by_id:
        user = by_id[next_appt["patient_id"]]
    else:
        user = await get_user_by_phone(phone) or (users[0] if users else {})

    patient_name = user.get("patient_name") or user.get("name") or ""
    return build_attributes(
        doctor_label=_doctor_label(user.get("doctor_id")),
        next_appt=next_appt,
        patient_name=patient_name,
        is_returning=user.get("is_returning_patient"),
        custom_price=user.get("custom_price"),
        tz=TZ,
    )


async def _sync_one(phone: str, now: datetime) -> None:
    """Reconcilia os custom attributes de um contato: só grava quando muda."""
    attrs = await _build_attrs_for_phone(phone, now)

    events = await get_events_by_type(phone, ATTR_EVENT, limit=1)
    last = (events[0].get("metadata") or {}).get("attrs") if events else None
    if not needs_attr_change(attrs, last):
        return

    contact_id = await find_or_create_contact_id(phone)
    if contact_id is None:
        print(f"  [attrs] sem contact_id no Chatwoot para {phone}")
        return

    try:
        await set_contact_custom_attributes(contact_id, attrs)
    except Exception as e:
        print(f"  [attrs] set falhou para {phone}: {type(e).__name__}: {e}")
        return  # não grava a memória — próximo run tenta de novo

    await log_event(ATTR_EVENT, phone, {"attrs": attrs, "contact_id": contact_id})
    print(f"  [attrs] atualizado {phone}: {attrs}")


async def main() -> None:
    from supabase import acreate_client

    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    phones = await candidate_phones(client, now)
    print(f"Contatos a sincronizar: {len(phones)}")
    for phone in phones:
        try:
            await _sync_one(phone, now)
        except Exception as e:
            print(f"  [attrs] erro inesperado em {phone}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
