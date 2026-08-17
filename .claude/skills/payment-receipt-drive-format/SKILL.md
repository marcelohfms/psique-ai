---
name: payment-receipt-drive-format
description: Use whenever renaming, naming, or referencing a payment receipt (comprovante de pagamento) file in Google Drive for this project, or when asked "qual o formato do comprovante de pagamento" / "como renomear o comprovante". Also use when writing or reviewing code that touches register_payment's Drive-rename logic in app/graph/tools.py, or a one-off script that registers a payment manually and needs the Drive filename to match what the bot would have produced.
---

# Payment Receipt (Comprovante) — Google Drive Filename Format

When `register_payment` (in `app/graph/tools.py`) processes a payment that includes a `drive_link`, it renames the underlying Google Drive file to this format (as of 2026-07-06):

```
{Nome_Do_Paciente}_{DD-MM-AAAA}_R${valor}
```

- `Nome_Do_Paciente` — the patient's full name with spaces replaced by `_` (accents are kept as-is).
- `DD-MM-AAAA` — the date of the appointment the payment is linked to, with `/` replaced by `-`. If there's no linked appointment, falls back to today's date.
- `valor` — the amount passed to `register_payment` (e.g. `100,00` or `R$ 100,00`), with any `R$` prefix and spaces stripped, and `,`/`.` replaced by `-` (e.g. `100-00`). If the amount wasn't identified (`amount="?"` or empty), uses the placeholder `valor-nao-identificado` instead of emitting a broken trailing `_R$.` / `_R$?.`.
- **No extension is appended here** — `rename_file` (in `app/google_drive.py`) fetches the file's current name from Drive first and reuses whatever extension it was actually uploaded with (`.jpg` or `.pdf`). Before 2026-07-06 the extension was hardcoded to `.jpg`, which mislabeled every PDF receipt.

**Example** (Amaury Ferreira De Lima Júnior's booking-fee receipt, PIX comprovante uploaded as jpg):

```
Amaury_Ferreira_De_Lima_Júnior_01-07-2026_R$100-00.jpg
```

## Where this comes from

`app/google_drive.py::build_receipt_filename(patient_name, appointment_dt, amount)` is the **single source of truth** for this name (as of 2026-08-17). Never rebuild it by hand — call it:

```python
from app.google_drive import build_receipt_filename, rename_file

new_filename   = build_receipt_filename(patient_name, appointment_dt, amount)  # no extension
final_filename = await rename_file(file_id, new_filename)  # returns e.g. "..._R$100-00.pdf"
```

`app/google_drive.py`'s `_rename_file` does the extension lookup and **returns the resolved name**:

```python
meta = service.files().get(fileId=file_id, fields="name").execute()
current_name = meta.get("name", "")
_, dot, ext = current_name.rpartition(".")
final_name = f"{new_name}.{ext}" if dot else new_name
```

That resolved name travels on to the Pagamentos sheet: `register_payment` passes it as
`append_payment_receipt(..., receipt_filename=final_filename)`, and the comprovante hyperlink in
column I displays it verbatim. Until 2026-08-17 `app/google_sheets.py` rebuilt its own version of
the name (comma kept in the amount, `.jpg` hardcoded), so the text in the sheet never matched the
real file and searching Drive by it found nothing. If `receipt_filename` is omitted (one-off
scripts, or a rename that failed) the sheet falls back to `build_receipt_filename` — the same stem,
minus the extension, never an invented one.

The rename only runs when a `drive_link` is present — payments registered without a receipt image (attendant-instructed, no proof) never trigger a Drive rename. If the rename call raises (e.g. Drive API hiccup), `register_payment` still succeeds but appends a warning to the clinic notification email so the mismatch doesn't go unnoticed — the `drive_link` itself still points to the right file regardless (Drive's `webViewLink` is keyed by file ID, not filename).

## When to apply this manually

If you register a payment through a one-off script (bypassing `register_payment`'s automatic Drive rename — e.g. because the payment came in via an attendant note with no image), and the receipt file still needs a matching name in Drive, call `build_receipt_filename` + `rename_file` instead of hand-rolling the format, and pass the returned name on as `append_payment_receipt(..., receipt_filename=...)` so the sheet shows the same thing:

```python
new_filename   = build_receipt_filename(patient_name, appointment_dt, amount)
final_filename = await rename_file(file_id, new_filename)
await append_payment_receipt(..., receipt_filename=final_filename)
```

Older one-off scripts under `scripts/` predate the helper and still inline the format — leave them as historical records, but don't copy from them.

**The attendant dashboard is a separate deployable** (`dashboard/`, own Dockerfile/pyproject, imports nothing from `app/`) and still has its own copies of this format in `dashboard/payments.py` (`upload_comprovante` and `_append_payment_sheet`), which do NOT agree with each other. Don't assume a file named by the panel follows the format above.
