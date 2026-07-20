# Roadmap: transformar o Psique em uma plataforma nos moldes do MedX Care

Este documento compara o que o MedX Care oferece hoje com o que o bot Psique já resolve, e lista o que falta desenvolver, em que ordem e como construir cada parte.

## Onde o Psique já está à frente

O bot Psique não é um chatbot genérico. Ele já tem regras de negócio específicas da clínica embutidas na lógica de conversa: duração de consulta diferente por idade do paciente, preferência entre Dr. Júlio e Dra. Bruna, memória de pacientes retornantes que evita repetir cadastro, e handoff para atendente humano via Chatwoot quando necessário. O MEDBOT do MedX Care é descrito como um assistente genérico de IA generativa, sem esse tipo de regra de negócio pré-modelada. Essa é a vantagem competitiva real do Psique e vale manter e evoluir esse motor em vez de substituí-lo por um assistente genérico, mesmo migrando o resto do sistema para o nível do MedX Care.

## Prontuário eletrônico

O MedX Care organiza o prontuário em fichas, receituário, exames, atestados, laudos, pareceres, orçamentos e termos. Hoje o Psique só tem solicitação de documentos (laudo, exame, relatório, receita, declaração), registrada em Google Sheets e Google Drive, sem um modelo de dados clínico nem uma tela de consulta para o médico.

O que falta: um conjunto de tabelas próprias no Supabase para registrar cada tipo de documento clínico como um registro estruturado ligado ao paciente (não um arquivo solto em uma planilha), mais uma interface para o médico criar e consultar esses registros. Vale começar pelas fichas de consulta e pelo receituário, que são usados em toda consulta, e só depois adicionar exames, atestados, laudos e pareceres, que são usados com menor frequência.

Como esse prontuário vai passar a ser o registro legal do atendimento, o desenho das tabelas precisa nascer já com log de auditoria (quem alterou o quê e quando), controle de acesso por perfil (médico só vê seus pacientes, atendente vê o que precisa e nada além disso), criptografia dos dados sensíveis e rotina de backup automático. Isso não é um adicional para depois, é requisito da Resolução CFM 1.821/2007 e do manual de certificação SBIS/CFM para o prontuário ter validade jurídica.

## Prescrição digital com assinatura ICP-Brasil

O MedX Care assina digitalmente receitas com certificado ICP-Brasil, o que é o que permite que o documento valha sem necessidade de impressão em papel. Existe uma API oficial para isso, a API de Assinatura Eletrônica do gov.br (Conecta gov.br), que já prevê explicitamente documentos de saúde (prescrição, atestado, solicitação de exame, relatório médico) com os metadados de OID e QR Code validável pelo ITI. Como alternativa paga, com SLA e suporte dedicado, existem provedores como Certisign, Bird ID e Soluti.

Recomendo construir isso como um serviço de assinatura único, chamado sempre que qualquer documento do prontuário é finalizado (receita, atestado, laudo), em vez de implementar assinatura separadamente para cada tipo de documento. Isso mantém a lógica de compliance concentrada em um único lugar, mais fácil de auditar e de trocar de provedor se necessário.

## Telemedicina integrada ao prontuário

Esse é o ponto que o cliente marcou como indispensável: ver o prontuário enquanto atende por vídeo. A chamada de vídeo em si não precisa ser construída do zero, existem SDKs embutíveis como Daily.co, Twilio Video, Agora ou Jitsi self-hosted. Daily.co ou Twilio são os caminhos mais rápidos de implementar, pois cuidam da infraestrutura de conexão (TURN/STUN) e cobram por minuto de uso. Jitsi self-hosted custa menos por minuto mas exige manter a própria infraestrutura de servidor de mídia, o que só compensa em escala alta.

O trabalho real de engenharia aqui é de interface: renderizar o painel de vídeo e o painel de prontuário lado a lado na mesma tela, ambos lendo e escrevendo no mesmo registro do paciente em tempo real, para o médico não precisar alternar entre abas ou sistemas durante a consulta.

## IA para interpretação de exames laboratoriais

O MedX Care usa IA para interpretar exames de laboratório enviados pelo paciente. Isso é construível com o mesmo stack que o Psique já usa, o GPT-4o já processa imagem e PDF, então dá para criar uma ferramenta (ou um novo nó no LangGraph) que recebe o exame anexado, extrai os valores e gera uma interpretação estruturada para o médico revisar antes de incorporar ao prontuário. Importante enquadrar isso como apoio à decisão do médico, não como diagnóstico automático, tanto por segurança do paciente quanto por responsabilidade legal.

## Agenda robusta e aplicativo mobile

Hoje o agendamento acontece via Google Calendar, acionado pelo bot. Falta uma visão de agenda dentro do próprio sistema, que leia e escreva na mesma fonte de dados usada pelo bot (decidir se o Google Calendar continua como fonte da verdade ou se migra para uma tabela própria), e acesso mobile. Para o mobile, faz mais sentido começar com uma versão web responsiva (ou PWA) antes de investir em aplicativos nativos para iOS e Android, que é um investimento bem maior e separado.

## Financeiro e controle de estoque

O dashboard já trata confirmação de pagamento via PIX (dashboard/payments.py). Falta um módulo financeiro completo, com faturamento e contas a receber por convênio ou particular, e controle de estoque, este último provavelmente de baixa prioridade para uma clínica de psiquiatria, que não costuma aplicar insumos no local como uma clínica cirúrgica ou dermatológica faria.

## Marketing, CRM e pesquisa de satisfação

Ainda não existe nada dedicado a isso no Psique. É a parte mais incremental do roadmap, campanhas de relacionamento e pesquisas de satisfação pós-consulta podem esperar o núcleo clínico (prontuário, telessaúde, assinatura digital) estar pronto.

## Ordem sugerida de desenvolvimento

1. Prontuário básico (fichas e receituário primeiro): é o núcleo de valor clínico e pré-requisito para a telessaúde fazer sentido, já que não há o que mostrar ao lado do vídeo sem ele.
2. Telessaúde integrada ao prontuário: indispensável para o cliente atual, e depende do prontuário básico já existir.
3. Assinatura digital ICP-Brasil para os documentos do prontuário: necessário para o prontuário valer como documento legal sozinho.
4. Agenda nativa dentro do sistema, substituindo ou absorvendo o Google Calendar.
5. IA de interpretação de exames: diferencial de produto, mas não bloqueia o uso diário da clínica.
6. Financeiro, estoque, marketing e CRM: podem vir depois, com menor urgência para o primeiro cliente.

O bot de atendimento (o equivalente ao MEDBOT) já está pronto e deve continuar sendo evoluído em paralelo, não pausado enquanto o resto é construído.
