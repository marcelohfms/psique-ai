"""Confere, só leitura, como os arquivos do Drive estão compartilhados.

Lista os arquivos mais recentes das pastas de pagamentos e de documentos e mostra
a permissão de cada um: se ainda tem link público (anyone) ou se está restrito às
contas da clínica (user). NÃO cria, move nem altera nada.

Uso (na raiz do projeto):
    uv run python scripts/_check_drive_sharing.py
"""
import os

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_RECENT = 8  # quantos arquivos recentes olhar por pasta


def _service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=_SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def _describe_permissions(service, file_id: str) -> str:
    res = service.permissions().list(
        fileId=file_id, fields="permissions(type,role,emailAddress)"
    ).execute()
    perms = res.get("permissions", [])
    publico = [p for p in perms if p.get("type") == "anyone"]
    users = [p.get("emailAddress") for p in perms if p.get("type") == "user"]
    if publico:
        return "PÚBLICO (anyone) <-- ainda exposto"
    if users:
        return "restrito: " + ", ".join(users)
    return "só a conta dona"


def _listar(service, folder_id: str, titulo: str) -> None:
    print(f"\n== {titulo} ==")
    if not folder_id:
        print("  (pasta não configurada nesta env)")
        return
    res = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        orderBy="createdTime desc",
        pageSize=_RECENT,
        fields="files(id,name,createdTime)",
    ).execute()
    files = res.get("files", [])
    if not files:
        print("  (nenhum arquivo)")
        return
    for f in files:
        estado = _describe_permissions(service, f["id"])
        print(f"  {f.get('createdTime','')}  {f.get('name','')}\n      {estado}")


def main() -> None:
    print("DRIVE_SHARE_EMAILS =", os.getenv("DRIVE_SHARE_EMAILS", "(vazio)"))
    service = _service()
    _listar(service, os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID", ""), "Comprovantes de pagamento")
    _listar(service, os.getenv("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID", ""), "Documentos do paciente")


if __name__ == "__main__":
    main()
