# Barrar comprovante PIX para chave que não é da clínica

**Data:** 2026-08-18
**Branch:** `reject-foreign-pix-key`

## Problema

A Eva registra qualquer comprovante como pagamento válido, mesmo quando o PIX foi
feito para uma chave que **não é a da clínica**. Caso real (18/08/2026, João Pedro
Lins / contato Reinaldo `5581992424522`): o paciente mandou um comprovante de um PIX
que fez **para a própria chave** (`+55 81 99242 4522`, destinatário "José Reinaldo da
Costa Gomes Filho") — dinheiro que nunca chegou na clínica — e a Eva registrou como
Taxa de Reserva paga. Só depois ele mandou o comprovante correto (chave
`42006848000178`, destinatário PSIQUE) pedindo para considerar só esse.

Consequência: taxa marcada como paga sem o dinheiro ter entrado; limpeza manual da
planilha Pagamentos + Drive depois.

## Objetivo

Impedir que o `register_payment` grave um pagamento quando o comprovante mostra,
de forma inequívoca, um destino diferente da chave da clínica
(`CORRECT_PIX_KEY = "42006848000178"`, em `app/graph/prompts.py`). Quando barrar,
a Eva avisa **o paciente** para refazer o PIX na chave certa. Nada é gravado.

## Decisões de comportamento (definidas com o usuário)

1. **Fail-open em caso ambíguo.** Só barra quando o comprovante mostra claramente
   OUTRA chave. Se o destino não for identificável (OCR não capturou, chave
   mascarada, sem token de chave legível) → registra como hoje. Evita travar
   comprovante legítimo mal escaneado.
2. **Destino aceito = apenas o CNPJ `42006848000178`.** O casamento é por dígitos,
   ignorando pontuação. (O nome "PSIQUE" não é usado como critério de aceite; na
   prática um comprovante legítimo sem chave estrangeira legível já passa pelo
   fail-open.)
3. **Só avisa o paciente.** Sem e-mail/evento para a clínica quando barra.
4. **Só vale para comprovante com imagem.** Pagamentos do painel/atendente
   (`is_link=True` ou `payment_method` preenchido, sem `image_description`) não são
   afetados — não há comprovante de imagem para inspecionar.

## Arquitetura

### Função pura de decisão

Nova função em `app/graph/tools.py` (ou helper próximo), pura e testável:

```python
def _receipt_destination_is_foreign(image_description: str) -> bool:
    """True somente quando o comprovante mostra INEQUIVOCAMENTE uma chave de
    destino diferente da clínica. Fail-open: retorna False em qualquer caso
    ambíguo (sem texto, sem token de chave, chave mascarada/curta)."""
```

Regra:

1. `full = re.sub(r"\D", "", image_description)` (todos os dígitos do texto).
   Se `"42006848000178"` estiver contido em `full` → clínica presente → `False`
   (registra). Cobre o CNPJ com/sem pontuação e mascarado que ainda contenha a
   sequência completa.
2. Senão, extrai o token de destino: o trecho após `"chave PIX"` **ou**
   `"CPF/CNPJ"` / `"CPF"` / `"CNPJ"` (case-insensitive) até a próxima vírgula,
   ponto-e-vírgula ou fim de linha; reduz a dígitos (`dest`).
   - Se `len(dest) >= 11` **e** `"42006848000178"` não é substring de `dest`
     **e** `dest` não é substring de `"42006848000178"` → **`True`** (barra).
   - Caso contrário → `False` (fail-open, registra).

O limiar de 11 dígitos evita falso positivo com máscara curta (ex.
`***.848.000/1-78` vira poucos dígitos e passa) e cobre chave-telefone (11 dígitos
com DDD, ou 13 com +55) e CPF (11) e CNPJ (14).

### Integração no `register_payment`

Logo após o bloco que resolve/recupera o `drive_link` e antes de qualquer escrita
(resolução de paciente, Drive rename, `append_payment_receipt`, update de
`booking_fee_paid_at`/`paid_at`):

```python
if image_description and not is_link and not payment_method:
    if _receipt_destination_is_foreign(image_description):
        _logger.warning("REGISTER_PAYMENT blocked: destino estrangeiro | desc=%r", image_description[:160])
        return (
            "⚠️ Esse comprovante foi para outra chave PIX, não para a da clínica. "
            "NÃO registrei o pagamento. Peça ao paciente para conferir e refazer o "
            "PIX para a chave 42006848000178 (CNPJ PSIQUE) e reenviar o comprovante."
        )
```

O retorno é a *string* que a Eva recebe como resultado da tool — ela então redige a
mensagem ao paciente com a chave correta. Nenhum efeito colateral acontece antes
desse ponto (o `drive_link` já foi só *resolvido*, não usado para renomear).

### Reforço no prompt (secundário)

Uma linha no bloco de pagamento do system prompt (`app/graph/prompts.py`) lembrando
a Eva de conferir o destinatário do comprovante antes de registrar, e de nunca
registrar PIX para chave diferente de `CORRECT_PIX_KEY`. É reforço; a trava na tool
é quem garante.

## Fluxo de dados

1. Paciente envia imagem → `app/media.py` gera `image_description`
   ("COMPROVANTE DE PAGAMENTO: ... chave PIX <x>, nome do destinatário <y>, ...").
2. Eva chama `register_payment(..., image_description=...)`.
3. `register_payment` resolve `drive_link` → chama `_receipt_destination_is_foreign`.
4. Estrangeiro → retorna aviso, nada gravado. Clínica/própria/ambíguo → segue o
   fluxo normal de registro.

## Tratamento de erro / bordas

- `image_description` vazio → função retorna `False` (fail-open). Guard extra:
  a integração só chama a função quando `image_description` está preenchido.
- Chave da clínica mascarada + sem chave estrangeira legível → fail-open registra.
- Comprovante que mostra tanto o pagador quanto o recebedor: se o CNPJ da clínica
  aparecer em qualquer lugar do texto, passo 1 já aceita; senão depende do token de
  destino. Risco residual de barrar um legítimo é aceitável — o paciente reenvia /
  a atendente registra manual pelo painel.

## Testes (`tests/test_tools.py`)

Unitários da função pura `_receipt_destination_is_foreign`:

1. Chave-telefone estrangeira (caso João Pedro: `chave PIX +55 81 99242 4522`) → `True`.
2. CNPJ da clínica com pontuação (`42.006.848/0001-78`) → `False`.
3. CNPJ da clínica sem pontuação (`42006848000178`) → `False`.
4. CNPJ mascarado curto sem chave estrangeira → `False` (fail-open).
5. `image_description` vazio → `False`.
6. CPF de terceiro (11 dígitos, ex. `123.456.789-00`) → `True`.

Integração de `register_payment` (mockado, no estilo dos testes existentes):

7. Comprovante com chave estrangeira → retorna o aviso, **não** chama
   `append_payment_receipt` nem renomeia Drive nem atualiza `booking_fee_paid_at`.
8. Comprovante com CNPJ da clínica → registra normalmente (comportamento atual
   preservado).
9. Pagamento do painel (`is_link=True`, sem `image_description`) → não afetado.

## Fora de escopo

- Aviso/evento para a clínica nos casos barrados (decidido: só o paciente).
- Casar por nome "PSIQUE" (decidido: só CNPJ; fail-open cobre o resto).
- Reprocessar retroativamente comprovantes já registrados.
