"""One-off: correct a bogus `appointment_rescheduled` event for phone 5581997828165.

On 2026-07-02, the bot called reschedule_appointment with the SAME start_time
(2026-07-06T14:00:00) just to change modality (presencial -> online), instead of
using change_modality. This logged an appointment_rescheduled event and consumed
the patient's one free reschedule, causing the bot to wrongly tell her on
2026-07-03 that a genuine reschedule request would require a new booking fee.

This script relabels that specific event to modality_changed so the reschedule
policy check (which counts appointment_rescheduled events) no longer misfires
for this patient.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997828165"
APPOINTMENT_ID = "nu6am5t74rm0ogf837jmdoujh8"


async def main():
    from app.database import get_supabase

    client = await get_supabase()

    res = await client.from_("events").select("*").eq("phone", PHONE) \
        .eq("event_type", "appointment_rescheduled").execute()
    print(f"Found {len(res.data)} appointment_rescheduled event(s) for {PHONE}")
    for e in res.data:
        print(f"  id={e['id']} created_at={e['created_at']} metadata={e['metadata']}")

    targets = [e for e in res.data if e.get("metadata", {}).get("appointment_id") == APPOINTMENT_ID]
    if not targets:
        print("No matching event found — nothing to fix.")
        return

    for e in targets:
        new_metadata = {
            "appointment_id": e["metadata"].get("appointment_id"),
            "new_modality": "online",
            "_corrected_from": "appointment_rescheduled",
            "_correction_reason": "reschedule_appointment was called with the same start_time to change modality only",
        }
        await client.from_("events").update({
            "event_type": "modality_changed",
            "metadata": new_metadata,
        }).eq("id", e["id"]).execute()
        print(f"Updated event {e['id']}: appointment_rescheduled -> modality_changed")

asyncio.run(main())
