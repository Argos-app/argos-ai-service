# Docker local — guia para agentes

O [guia integrado](docker-local.md) descreve o Compose da raiz, modelos
simulados, configuração, builds, dados sintéticos e verificações. `postgres/`
contém a imagem e os scripts SQL usados pela infraestrutura local.

Antes de alterar inicialização, credenciais ou volumes, confira `compose.yaml`,
os scripts em `scripts/docker`, os Dockerfiles e os testes de integração dos
serviços. Preserve a separação entre segredos locais e arquivos versionados.
