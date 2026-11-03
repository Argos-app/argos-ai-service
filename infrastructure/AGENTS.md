# Infraestrutura de dados local — guia para agentes

`docker/postgres/Dockerfile.local` e `docker/postgres/init` montam o PostgreSQL
usado na pilha local. Os módulos SQL versionados, dados e fixtures locais são
consumidos pela imagem e por testes de integração do MCP de dados. Não trate
dados sintéticos como dados de produção.

Ao mudar schema, permissões, ordem dos scripts ou fixtures, revise o Compose da
raiz, o script de seed, o MCP de dados, o MCP de predição e seus testes de
integração. O leitor dos serviços usa acesso limitado; não remova os controles
read-only. Comandos e contexto em [Docker local](docker/README.md) e nos READMEs
de integração dos serviços.

O [guia de Docker](docker/AGENTS.md) trata da pilha integrada; o
[guia Mongo](mongodb/AGENTS.md) reúne configuração, estrutura e consumo de
dados.
