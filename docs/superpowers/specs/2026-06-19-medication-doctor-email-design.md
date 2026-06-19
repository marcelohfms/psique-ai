# Spec: Problema com Medicação / Quer Falar com o Médico → E-mail do Médico

**Data:** 2026-06-19

## Contexto

Quando um paciente relata problema com sua medicação (efeitos colaterais, dúvidas sobre dose, como tomar) ou diz que precisa/quer falar com o médico, Eva deve orientá-lo a enviar um e-mail diretamente ao médico responsável — em vez de transferir para atendente humana.

## Gatilhos

1. **Problema com medicação** — paciente menciona efeitos colaterais, dúvida sobre dose, como tomar, ou qualquer questão clínica sobre o tratamento em curso.
2. **Quer falar com o médico** — paciente diz que precisa ou quer falar diretamente com o médico.

> Não se confunde com "receita com problema na farmácia" (rasura, vencida, recusada), que continua indo para `transfer_to_human`.

## Fluxo

### Se `preferred_doctor` já está definido
Eva responde com empatia, orienta a enviar e-mail e fornece o endereço:
- Dr. Júlio → `dr.juliogouveia@gmail.com`
- Dra. Bruna → `brunalima.psiquiatra@gmail.com`

### Se `preferred_doctor` não está definido
Eva pergunta: "Qual médico te acompanha? Dr. Júlio ou Dra. Bruna?"
Após a resposta, fornece o e-mail correspondente.

## Implementação

Mudança exclusiva de prompt: nova seção em `MEDICAL_LIMITS_RULE` em `app/graph/prompts.py`.

Nenhuma alteração de código, ferramenta ou estado é necessária.
