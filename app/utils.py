_COMPOUND_FIRST_NAMES = {"maria", "ana", "joão", "joao", "josé", "jose"}
_LINKING_WORDS = {"de", "do", "da", "dos", "das", "e"}


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
