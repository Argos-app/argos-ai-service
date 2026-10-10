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

Além dos testes de componentes, `test_a2a_runtime.py` sobe um processo servidor
A2A filho, conecta-o ao Qdrant descartável e envia mensagens pelo cliente
oficial do SDK. Os adapters de embeddings, geração de resposta e permissão são
determinísticos e exclusivos de teste. A suíte cobre as skills Legal, Documental
e a composição `mixed` (consulta combinada de leis e termos), concorrência,
autenticação de serviço e delegação, revogação de permissão, isolamento de tasks
por usuário, atualização de revisão e reinício do processo. Após a sincronização
pelo A2A, também exercita os casos de uso de trecho detalhado e agregação de
documentos distintos sobre o repositório ligado ao mesmo alias Qdrant.

O catálogo dos PDFs de fixture é global e não contém escopo de propriedade; por
isso a homologação verifica a ausência de metadados de propriedade no Qdrant e
testa isolamento de tasks por usuário, sem simular isolamento multi-propriedade.
O teste continua local: não executa o principal, não consulta cloud nem
provedores reais, e não valida o adapter FAIIS ou geração de linguagem de
produção. As chaves e o volume são exclusivos de teste; não reutilize em outros
ambientes.
