# Homologação isolada do A2A documental

Os testes exercitam o Qdrant real com credenciais separadas de leitura e
escrita, PDFs sintéticos allowlisted e embeddings determinísticos. Usam somente
o container descartável deste diretório e arquivos temporários; não acessam
cloud, LLM, bancos de negócio ou coleções de desenvolvimento.

Execute na raiz do repositório com Docker em execução:

```powershell
python -m venv .venv/a2a-integration
.venv/a2a-integration/Scripts/python.exe -m pip install -r apps/a2a_agent/tests/integration/requirements.txt
docker compose -f apps/a2a_agent/tests/integration/compose.yaml up -d
$env:A2A_RUN_INTEGRATION="1"
.venv/a2a-integration/Scripts/python.exe -m pytest apps/a2a_agent/tests/integration -v -p no:cacheprovider
Remove-Item Env:A2A_RUN_INTEGRATION
docker compose -f apps/a2a_agent/tests/integration/compose.yaml down
```

Sem `A2A_RUN_INTEGRATION=1`, os testes são ignorados na suíte habitual. Quando
habilitados, indisponibilidade do Qdrant ou chave inválida falha o teste; não há
resultado aprovado por skip. A API de leitura recebe somente a chave
`READ_ONLY_API_KEY`; testes tentam `upsert`, `delete` e criação de coleção com
essa credencial e exigem resposta de autorização negada. A chave de escrita é
usada somente pelo writer no teste.

O harness comprova a integração implementada entre fonte local, parser PDF,
embeddings stub, sincronizador, publicação do alias e leitor Qdrant. O serviço
A2A de tarefas, os agentes Documental/Legal, os casos de busca/agregação, a
autorização delegada do chamador e a reinicialização do processo servidor ainda
não existem nesta revisão e não são afirmados por estes testes. As chaves e o
volume são exclusivos de teste local; não reutilize em outros ambientes.
