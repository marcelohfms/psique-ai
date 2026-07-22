# Prompt para o Hostinger Horizons — MVP do painel clínico Psique

Prompt pronto para colar no assistente de IA do Horizons, cobrindo o escopo do MVP definido em docs/roadmap-medx-parity.md (cadastro, agenda, prontuário básico, telessaúde integrada). Ajuste o nome do app e o texto entre colchetes antes de usar.

---

Crie um aplicativo web (SaaS) de gestão clínica para uma clínica de psiquiatria, chamado [nome do app], que serve como painel operacional para médicos e atendentes. Este app precisa funcionar de forma integrada a um chatbot de WhatsApp já existente (construído em Python, FastAPI e LangGraph), que hoje já cadastra pacientes e agenda consultas escrevendo diretamente em um banco de dados Supabase.

Fonte de dados: conecte este app ao mesmo projeto Supabase (PostgreSQL) que o bot já usa, em vez de criar um banco de dados novo. As tabelas de pacientes, contatos e médicos já existem e devem ser lidas e atualizadas por este painel, não duplicadas. Vou fornecer a URL e a chave do projeto Supabase durante a configuração.

Funcionalidades da primeira versão, nesta ordem de prioridade:

Autenticação com dois perfis de acesso, médico e atendente, com permissões diferentes. O médico vê o prontuário completo dos seus pacientes. O atendente vê cadastro e agenda, mas não o conteúdo clínico do prontuário.

Lista de pacientes, com busca por nome, e uma ficha por paciente mostrando dados pessoais (nome, idade, CPF, telefone, convênio ou particular) e uma linha do tempo simples com os eventos principais, como consultas agendadas e documentos emitidos.

Prontuário básico dentro da ficha do paciente, com duas seções: fichas de consulta (texto estruturado por data, anotado pelo médico durante ou após o atendimento) e receituário (lista de receitas emitidas, com nome do medicamento, posologia e data).

Agenda com visualização de calendário das consultas já agendadas pelo bot (lidas do Supabase), permitindo ao médico e ao atendente ver os horários do dia e da semana e o status de cada consulta.

Telessaúde: dentro da ficha do paciente, um botão para iniciar chamada de vídeo que abre a videochamada e o prontuário lado a lado na mesma tela, sem precisar trocar de aba. Use uma integração de vídeo via API (por exemplo Daily.co) para a chamada em si.

Fora do escopo desta primeira versão: não construir módulos de financeiro, faturamento por convênio (TISS), marketing ou relatórios avançados agora.

Tom visual: interface limpa, profissional e acolhedora, com cores calmas, evitando vermelho e tons agressivos, considerando que é uma clínica de saúde mental. Tipografia legível, sem parecer um sistema corporativo genérico.

Cada nova clínica que contratar este produto vai rodar sua própria instância do app com seu próprio projeto Supabase, então a configuração da conexão de dados deve ficar em variáveis de ambiente ou configuração, nunca fixa no código.

---

## Ajuste enviado depois: trocar PocketBase por Supabase Auth

O Horizons entregou a primeira versão usando PocketBase para autenticação interna (médico/atendente), separado do Supabase. Prompt para pedir a troca:

Troque o sistema de autenticação interna do app. Hoje o app usa PocketBase para gerenciar os logins de médico e atendente, separado do Supabase. Quero que a autenticação use o Supabase Auth do mesmo projeto que já guarda os dados clínicos, não um sistema à parte.

Motivo técnico: os dados de paciente vão ficar protegidos por Row Level Security (RLS) no Supabase, e as políticas de RLS só conseguem identificar quem está fazendo a requisição, para aplicar a regra de "médico só vê seus próprios pacientes", se o login vier do próprio Supabase Auth, usando auth.uid() e o token emitido por ele. Com PocketBase separado, o Supabase não tem como saber qual médico está logado, e a RLS não consegue proteger os dados por usuário.

Ajustes necessários: substitua a criação e gestão dos usuários médico e atendente para o Supabase Auth (e-mail e senha), em vez do PocketBase. Mantenha os dois perfis de acesso e as permissões já definidas, médico vendo o prontuário completo dos seus pacientes e atendente vendo cadastro e agenda mas não o conteúdo clínico. Recrie as credenciais de teste como usuários no Supabase Auth, apontando para os mesmos perfis de permissão. Depois da troca, monte também as políticas de RLS nas tabelas patients, contacts, patient_contacts, appointments, doctors e documents, baseadas no perfil do usuário logado, de forma que o médico só veja os pacientes vinculados a ele via doctor_id.
