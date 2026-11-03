# Segurança — guia para agentes

`access`, `access_policy`, `access_directory`, `delegation`, `signatures` e
`adapters` implementam políticas, verificação de delegação/assinatura e acesso
cadastral. Os serviços consumidores aplicam essas peças aos seus contextos;
verifique também suas políticas locais e testes.

Preserve falha fechada, emissor, audiência, validade, chaves confiáveis,
revalidação de usuário e escopo por propriedade onde exigidos. Não confunda
token interno de serviço, delegação de usuário e assinatura de relatório. Não
registre segredos ou tokens. Mudanças afetam Main API, MCPs e A2A; execute
testes de cada consumidor relevante e as integrações que exercitam o contrato.
