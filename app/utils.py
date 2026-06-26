_COMPOUND_FIRST_NAMES = {"maria", "ana", "joão", "joao", "josé", "jose"}


def display_name(full_name: str) -> str:
    """Return the name to use when addressing a contact/patient.

    For names starting with Maria, Ana, João or José (very common in Brazil
    and usually paired with a second name), returns the first two words.
    For all other names, returns only the first word.
    """
    if not full_name:
        return full_name
    parts = full_name.split()
    if len(parts) >= 2 and parts[0].lower() in _COMPOUND_FIRST_NAMES:
        return f"{parts[0]} {parts[1]}"
    return parts[0]
