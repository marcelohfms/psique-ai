from scripts._migrate_marker_from_agendamento import decide_updates


def _row(role, is_self, rel, pid="p1", cid="c1"):
    return {"patient_id": pid, "contact_id": cid, "role": role,
            "is_self": is_self, "relationship": rel}


def test_propagates_agendamento_to_divergent_roles():
    rows = [_row("agendamento", False, "mãe"),
            _row("consulta", True, "mãe"),
            _row("financeiro", True, "mãe")]
    ups = decide_updates(rows)
    # duas linhas (consulta, financeiro) alinhadas ao agendamento
    assert {(u["role"], u["is_self"], u["relationship"]) for u in ups} == {
        ("consulta", False, "mãe"), ("financeiro", False, "mãe")}


def test_no_update_when_already_consistent():
    rows = [_row("agendamento", True, "self"),
            _row("consulta", True, "self"),
            _row("financeiro", True, "self")]
    assert decide_updates(rows) == []


def test_skips_pair_without_agendamento():
    rows = [_row("consulta", True, "mãe"), _row("financeiro", True, "mãe")]
    assert decide_updates(rows) == []
