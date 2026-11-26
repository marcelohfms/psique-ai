# Cadastro de paciente gerenciado pelo chat (Caminho 2)

Data: 2026-11-26
Caso que motivou: Helder (contato 12033646976), 26/11/2026.

## Intenção e o porquê

Um contato que JÁ é paciente da clínica (uma ficha no número) pediu para marcar
consulta para outra pessoa, o pai. O pai não tinha ficha. Hoje não existe nenhum
caminho, dentro da conversa, para criar uma ficha nova quando o contato já tem a
dele. Tanto o fluxo de cadastro (`collect_info_node`) quanto o agente
(`patient_agent_node`) resolvem o paciente pelo telefone e atualizam a única ficha
existente. Resultado: a `confirm_appointment` ignorou o nome do pai e marcou a
consulta de 02/12 dentro da ficha do filho (patient_id `bd2c5b52...`), e ainda
insistiu em "Helder Siqueira Maciel Júnior" mesmo depois de o cliente corrigir três
vezes.

A causa raiz está em `app/graph/tools.py`. A `confirm_appointment` só pergunta "para
qual paciente?" e só olha o `patient_name_override` quando o telefone tem mais de uma
ficha (`len(all_users) > 1`). Com uma ficha só, `_resolve_patient_for_booking` cai
direto em `get_user_by_phone` e devolve a ficha existente, descartando o nome
informado.

Queremos que a Eva consiga cadastrar um paciente novo gerenciado (adulto) pelo chat
e agendar para ele, sem nunca carimbar a consulta na ficha errada.

## Objetivos

- Um contato (seja paciente de si ou não) consegue cadastrar um paciente NOVO ADULTO
  que ele gerencia no mesmo número, e agendar para esse paciente.
- Trava determinística: a `confirm_appointment` nunca marca uma consulta numa ficha
  existente quando o nome informado se refere a alguém sem cadastro exato naquele
  contato.
- Cadastro completo do novo paciente antes de agendar: nome completo, data de
  nascimento, se já é paciente da clínica, CPF e e-mail (decisão do usuário).

## Não-objetivos (v1)

- Cadastrar paciente MENOR de 18 pelo chat. Menor dispara regra de 2h, responsável
  legal e CPF do responsável. Na v1, ao detectar menor, a Eva transfere para a
  atendente humana (decisão do usuário).
- Corrigir o caso Helder já gravado. Isso é manual e está esperando os dados
  cadastrais do pai.
- Mudar como o primeiro contato (lead novo) é cadastrado. O `collect_info_node`
  continua igual.

## Anti-objetivos (o que faria isso falhar mesmo "funcionando")

- Criar uma SEGUNDA ficha para o MESMO paciente (duplicata). Se o pai já for paciente
  da clínica sob outro número, tem que reconciliar por nome + nascimento, não criar
  ficha nova.
- Marcar na ficha errada (o bug original). A trava e o insert têm que concordar sempre
  sobre para quem é a consulta.
- Deixar o paciente adulto gerenciado sem receber lembrete nenhum (ver papéis abaixo).

## Restrições

- Toda a feature nasce numa git worktree própria (CLAUDE.md), nunca na `main`.
- Testes obrigatórios (CLAUDE.md): atualizar o que quebrar e cobrir o novo
  comportamento em `tests/test_tools.py` e `tests/test_process_message.py`.
- Paridade guard x insert, mas só na DESAMBIGUAÇÃO: quando o telefone tem mais de uma
  ficha e é preciso escolher qual, a trava e o insert continuam usando as MESMAS funções
  (`_match_patient_by_name` / `_resolve_patient_for_booking`), senão voltam a divergir
  (lição do caso Dione/Pedro Lins). Essa paridade NÃO vale para a pergunta nova "essa
  pessoa tem ficha?" (ver "Resolvido na revisão", ponto 1).
- Regra de ouro de nomes: falso negativo (travar um agendamento legítimo e a Eva
  reconfirmar) é barato; falso positivo (agendar no paciente errado) é caro. Na dúvida,
  travar.

## Abordagens consideradas

1. **Travar e passar para a atendente.** Só a trava determinística; a Eva transfere o
   caso de paciente novo. Menor mudança, mas não resolve o pedido do cliente pelo chat.
2. **Travar e cadastrar pelo chat (ESCOLHIDA).** A trava mais uma ferramenta nova que
   cria a ficha do paciente gerenciado e segue com o agendamento. Mais trabalho, mas é
   o que o cliente pediu.
3. **Só instrução no texto da Eva.** Sem trava no código. Descartada: a Eva já tinha
   entendido que era o pai e mesmo assim marcou no filho; texto sozinho não é
   determinístico.

Escolhido: Caminho 2.

## Como vai ser feito (decisões tomadas)

### 1. Trava determinística na `confirm_appointment`

Antes de criar o evento e o insert, com o telefone já resolvido:

- Buscar `all_users` do telefone (já é feito).
- Se `patient_name_override` foi passado e NÃO casa EXATAMENTE (nome civil ou nome
  social, normalizado com remoção de acento e caixa) com nenhuma ficha do telefone,
  então NÃO agendar na ficha existente. Devolver uma instrução interna pedindo para a
  Eva cadastrar o paciente primeiro (chamar a ferramenta nova) e só então rechamar a
  `confirm_appointment`.
- Match EXATO de propósito, não substring. O nome do pai ("Helder Siqueira Maciel") é
  prefixo do nome do filho ("Helder Siqueira Maciel Júnior"); o substring atual casaria
  errado e marcaria no filho.
- Manter a rede multi-paciente que já existe (quando `len > 1` e o override não
  singulariza ninguém, pergunta o nome completo).

Observação: quando o override está vazio (autoagendamento normal do próprio paciente),
nada muda; a ficha única continua sendo usada. Ver "Resolvido na revisão", pontos 1 e 2,
para por que isso NÃO quebra o agendamento comum.

### 2. Ferramenta nova `register_managed_patient`

Assinatura (campos preenchidos pela Eva perguntando ao contato):
`register_managed_patient(patient_name, birth_date, is_returning_patient, patient_cpf, patient_email, relationship)`.

Comportamento:

- Validar `patient_name` com `looks_like_name`. Lixo volta instrução para reperguntar.
- Calcular a idade pela data de nascimento (código, data de Recife, nunca a LLM).
  Se idade < 18, NÃO cadastrar e NÃO confiar no texto: a própria tool dispara a
  transferência para a atendente de forma determinística, reusando o caminho interno do
  `transfer_to_human` (pausa/handoff), e os dados parciais do menor ficam só no log para a
  atendente retomar. (Ver "Resolvido na revisão", ponto 4.)
- Criar uma ficha NOVA (insert forçado, sem resolver por telefone, para não cair na
  ficha do contato). Reconciliar retornante por nome + nascimento
  (`_reconcile_returning_patient`) para não duplicar paciente já existente sob outro
  número.
- Vincular contato (quem escreve) ao novo paciente com `is_self=False`, `relationship`
  informado, nos três papéis `agendamento`, `financeiro` e `consulta`:
  - `agendamento`: para o mapeamento telefone→paciente funcionar quando o contato
    escreve.
  - `financeiro`: para a cobrança da taxa/consulta chegar em quem paga (o filho disse
    que vai pagar).
  - `consulta`: para os lembretes de consulta/retorno chegarem, já que o paciente
    adulto gerenciado não tem contato próprio (sem isso, ninguém recebe lembrete).
  (Base: memória `roles-terceiro-consulta-vs-financeiro`.)
- NÃO tentar mutar o estado do grafo. As tools deste projeto devolvem texto e não mutam
  state; a `confirm_appointment` resolve o paciente AO VIVO do banco
  (`get_users_by_phone` / `_resolve_patient_for_booking`). Então o contrato é: a tool grava
  a ficha e o vínculo no banco e DEVOLVE o nome exato do novo paciente; a Eva, no MESMO
  turno, chama `confirm_appointment` com esse nome em `patient_name_override`, e aí a
  resolução ao vivo acha a ficha nova e agenda no pai. (Ver "Resolvido na revisão", ponto 3.)

A ferramenta entra na lista `TOOLS` em `app/graph/nodes.py`.

### 3. Prompt do agente

Acrescentar regra em `EXISTING_PATIENT_SYSTEM` (e onde o agente lê para pacientes
conhecidos) dizendo: quando o contato quiser agendar ou pagar para alguém que ainda não
é paciente cadastrado naquele número, colete o cadastro completo (nome completo,
nascimento, se já é paciente, CPF, e-mail), chame `register_managed_patient` e só então
`confirm_appointment` com o nome do novo paciente. Se for menor de 18, transfira para a
atendente.

### Acoplamento de idade (nota de implementação)

A `confirm_appointment` decide 1h/2h e `is_minor_first` pela `state["patient_age"]`, que
numa conversa de paciente conhecido é a idade do CONTATO. Na v1 (adulto-só) isso não
quebra, porque ambos são adultos e `is_minor_first` já exige idade < 18. Como a tool não
muta estado, `patient_age` segue sendo a do contato; tudo bem na v1. O caso de menor
gerenciado fica fora da v1 justamente porque esse acoplamento precisaria ser refeito (ler
a idade da ficha agendada, não do estado).

O e-mail do convite do Google Calendar também sai de `state["patient_email"]`, que é o do
contato (filho), não o do novo paciente. Na v1 isso é aceitável: o convite vai para quem
gerencia e paga. O e-mail do pai fica gravado na ficha dele mesmo assim. Se no futuro o
convite tiver que ir para o paciente, a `confirm_appointment` passa a ler o e-mail da
ficha resolvida quando houver override.

## Resolvido na revisão (builder-check)

1. **Match exato x paridade.** A pergunta "essa pessoa tem ficha neste contato?" usa um
   helper NOVO de match EXATO (nome civil ou social, com acento normalizado, reusando a
   normalização de `app/patients.py`). A `_match_patient_by_name` fica como está (com a
   passada por substring) só para DESAMBIGUAR entre fichas já existentes. Isso evita o bug
   do prefixo (o nome do pai é prefixo do nome do filho, e o substring casaria errado) sem
   regredir o agendamento multi-paciente. A trava só BLOQUEIA quando o match exato não acha
   ninguém; quando acha, a resolução segue igual a hoje.

2. **Não quebra o agendamento comum.** O prompt (prompts.py:680) já manda o agente passar
   `patient_name_override` com o nome EXATO do resumo "Paciente: ...". Para um paciente
   existente, esse nome é o `patients.name` canônico, então o match exato casa e agenda
   normal. A trava só dispara quando o override vem preenchido E não casa exatamente com
   nenhuma ficha. Pela regra de ouro, um raro falso bloqueio só faz a Eva reconfirmar o
   nome, o que é barato.

3. **Sem mutar estado.** Confirmado no código: tools devolvem texto e a
   `confirm_appointment` resolve o paciente ao vivo do banco. O fluxo é cadastrar (grava no
   banco) e, no mesmo turno, chamar `confirm_appointment` com o nome do novo paciente em
   `patient_name_override`.

4. **Menor é transferência determinística.** A tool executa o handoff ela mesma (não confia
   no texto do modelo), e não cria a ficha do menor.

Confirmado pelo revisor e sem ação necessária: o lembrete do adulto gerenciado chega ao
contato que fez a reserva (o insert grava `contact_id`), e a deduplicação do paciente já
sai de graça no `upsert_patient` (reconcilia por nome + nascimento quando há `birth_date`,
sem resolver por telefone).

## O que fica a cargo de quem implementar

- Texto exato das mensagens da Eva e das instruções internas.
- Reaproveitar a validação de CPF do `collect_info` (incluindo o "N/A" para
  estrangeiro) em vez de reescrever.
- Nome interno da função helper de match exato (civil/social) e onde colocá-la para a
  trava e a ferramenta compartilharem.
- Testes em `tests/test_tools.py` (trava bloqueia override de não cadastrado; exato x
  substring no caso Helder prefixo; `register_managed_patient` cria ficha distinta +
  vínculo + papéis; menor vira transferência; reconciliação de retornante; depois do
  cadastro, `confirm_appointment` marca no pai, não no filho) e
  `tests/test_process_message.py` (fluxo mockado fim a fim: filho pede para o pai, Eva
  coleta, cadastra e agenda no pai).
