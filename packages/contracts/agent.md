# Contratos — guia para agentes

Os modelos em `a2a.py`, `documents.py`, `email.py`, `lot_report.py` e
`prediction.py` definem mensagens trocadas entre Main API, MCPs e A2A. Valide os
campos e invariantes no código; `__init__.py` contém os exports públicos.

Campos, enums, validações e serialização podem afetar serviços implantados em
tempos diferentes e documentos persistidos. Antes de alterá-los, examine todos
os produtores, consumidores e testes; preserve compatibilidade quando
necessária. Em particular, não altere a representação assinada do envelope de
lote sem examinar emissor, verificador e consumidores. Execute os testes do
pacote e os dos serviços afetados. Comandos em [README.md](README.md).
