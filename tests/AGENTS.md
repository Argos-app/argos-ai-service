# Testes transversais — guia para agentes

Este diretório contém testes do planejamento e execução de deploy Render. As
suítes dos serviços ficam em `apps/<serviço>/tests`; contratos e observabilidade
possuem testes próprios em `packages`.

A CI executa `python -m pytest tests apps packages -m "not integration"` e roda
integrações em jobs próprios. Consulte os READMEs de integração antes de iniciar
containers: eles usam bancos e dados descartáveis. Para mudanças no deploy,
execute `python -m pytest tests/test_render_deploy.py -q` e preserve os cenários
de seleção por alterações e falha segura.
