# Kubernetes local — guia para agentes

Este diretório contém recursos Kustomize para namespace, storage, PostgreSQL,
dependências, aplicações e probes locais. A preparação de segredos e o build das
imagens ficam em `scripts/kubernetes`; siga [o guia Kubernetes](README.md) para
pré-requisitos e comandos.

Preserve os limites entre segredos locais e configuração versionada. Mudanças em
portas, imagens, probes ou dependências devem ser conferidas contra Dockerfiles,
`compose.yaml`, scripts e runtimes atuais. Renderizar recursos não comprova que
um cluster executou ou que serviços externos estejam disponíveis.
