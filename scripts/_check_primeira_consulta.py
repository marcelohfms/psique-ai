"""Verifica se os agendamentos futuros têm flag de primeira consulta."""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

PENDENTES = {
    "Gabriela Cardeal Queiroz de Brito", "Vicente Ximênes Lopes Novaes Gonçalves",
    "Guilherme Nogueira Cavalcanti Lopes Duzi", "Paula Muniz Evangelista",
    "Gabriel Galindo de Souza", "Pedro Lins De Araújo", "Maria Silvânia Gomes Da Costa",
    "Marina Leal Ribeiro", "Celyane Lacerda Montarroyos De Paula",
    "Danniela Azevedo Ramos De Almeida", "Ayexa Ferro Buarque Tavares",
    "Clarice Izabela Alves Gomes", "Augusto Carlos de Oliveira e Silva Neto",
}


async def main():
    from supabase import acreate_client
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Uma linha de appointment para inspecionar colunas
    sample = (await client.from_("appointments").select("*").limit(1).execute()).data
    cols = list(sample[0].keys()) if sample else []
    print("Colunas de appointments:")
    print(" ", cols)
    flag_cols = [c for c in cols if any(k in c.lower() for k in
                 ("primeira", "first", "retorno", "tipo", "type", "kind", "categoria"))]
    print("\nColunas candidatas a 'primeira consulta':", flag_cols or "NENHUMA")

    now_iso = datetime.now(timezone.utc).isoformat()
    appts = (
        await client.from_("appointments")
        .select("*")
        .in_("status", ["scheduled", "pending_reschedule"])
        .gte("end_time", now_iso)
        .order("start_time")
        .execute()
    ).data

    # mapear patient_id -> nome
    pats = (await client.from_("patients").select("id, name").execute()).data
    name_by_id = {p["id"]: p.get("name") for p in pats}

    if not flag_cols:
        print("\n(Não há coluna de primeira consulta na tabela appointments.)")
        return

    print(f"\n=== Valor da(s) flag(s) {flag_cols} nos agendamentos futuros ===\n")
    for a in appts:
        nome = name_by_id.get(a["patient_id"], "?")
        vals = {c: a.get(c) for c in flag_cols}
        mark = "  <-- PENDENTE" if nome in PENDENTES else ""
        when = a["start_time"][:16].replace("T", " ")
        print(f"{when}  {nome:45} {vals}{mark}")


asyncio.run(main())
