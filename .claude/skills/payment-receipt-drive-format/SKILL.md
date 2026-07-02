---
name: payment-receipt-drive-format
description: Use whenever renaming, naming, or referencing a payment receipt (comprovante de pagamento) file in Google Drive for this project, or when asked "qual o formato do comprovante de pagamento" / "como renomear o comprovante". Also use when writing or reviewing code that touches register_payment's Drive-rename logic in app/graph/tools.py, or a one-off script that registers a payment manually and needs the Drive filename to match what the bot would have produced.
---

# Payment Receipt (Comprovante) — Google Drive Filename Format

When `register_payment` (in `app/graph/tools.py`) processes a payment that includes a `drive_link`, it renames the underlying Google Drive file to this exact format:

```
{Nome_Do_Paciente}_{DD-MM-AAAA}_R${valor}.jpg
```

- `Nome_Do_Paciente` — the patient's full name with spaces replaced by `_` (accents are kept as-is).
- `DD-MM-AAAA` — the date of the appointment the payment is linked to, with `/` replaced by `-`. If there's no linked appointment, falls back to today's date.
- `valor` — the amount passed to `register_payment` (e.g. `100,00`), with any `R$` prefix and extra spaces stripped.
- The extension is always `.jpg`, regardless of the original file's real format — this is hardcoded, not detected.

**Example** (real case, Amaury Ferreira De Lima Júnior's booking-fee receipt):

```
Amaury_Ferreira_De_Lima_Júnior_01-07-2026_R$100,00.jpg
```

## Where this comes from

`app/graph/tools.py`, inside `register_payment`, under the "Rename Drive file" section:

```python
safe_name    = patient_name.replace(" ", "_")
new_filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
await rename_file(file_id, new_filename)
```

This only runs when a `drive_link` is present — payments registered without a receipt image (attendant-instructed, no proof) never trigger a Drive rename.

## When to apply this manually

If you register a payment through a one-off script (bypassing `register_payment`'s automatic Drive rename — e.g. because the payment came in via an attendant note with no image), and the receipt file still needs a matching name in Drive, reproduce this exact format so the file stays consistent with what the bot would have named it. Use `app/google_drive.py`'s `rename_file(file_id, new_filename)` to apply it.
