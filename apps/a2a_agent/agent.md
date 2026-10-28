# A2A documental — guia para agentes

## Fluxo e estrutura

O serviço recebe uma consulta A2A, valida credencial de serviço e delegação,
confere acesso cadastral e consulta fontes documentais. `agents`, `application`,
`domain` e `ports` definem especialistas, casos de uso e interfaces; `adapters`
e `infrastructure` fornecem HTTP, arquivos, PostgreSQL e Qdrant. `bootstrap.py`
monta o runtime e `server.py` expõe o transporte.

## Regras a preservar

- As skills documental e legal usam fontes e páginas do corpus; os PDFs de
  demonstração são sintéticos, não evidência jurídica real.
- A sincronização usa manifesto, revisão e alias no Qdrant. Embeddings principal
  e fallback ocupam vetores nomeados próprios, com dimensões compatíveis; não
  consulte um espaço com o vetor do outro nem sirva revisão antiga após falha.
- Preserve credenciais Qdrant distintas para leitura e escrita, validação de
  delegação `documents.read` e reconsulta de usuário/permissão no PostgreSQL.
- Tasks usam `InMemoryTaskStore` e não sobrevivem ao reinício. Não documente
  persistência de tasks que o processo não implementa.
- Logs, observações e tracing têm políticas próprias de minimização; não inclua
  prompts, documentos ou credenciais sem respeitar a configuração vigente.

## Dependências e testes

O processo usa Qdrant, PostgreSQL, corpus local e providers de embeddings e
geração; MongoDB, Redis e LangSmith são opcionais para observabilidade. Importa
os pacotes comuns `contracts`, `security` e `observability`. Consulte
[README.md](README.md) para execução, protocolo e testes com Qdrant descartável.
