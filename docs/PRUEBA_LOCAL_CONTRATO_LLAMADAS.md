# Prueba local del contrato de llamadas

No requiere una llamada real ni abre la SQLite personal. El runner construye una stanza
sintética con la forma documentada por el puente, valida el namespace y el envelope v1,
y usa una base temporal para comprobar persistencia y agregados.

```powershell
conda run -n XMPP python tests\run_call_contract_fixture.py
```

Resultado esperado:

```text
OK: parsed documented stanza, persisted one call, aggregate duration=120 seconds
```

Para la cobertura focalizada completa:

```powershell
conda run -n XMPP python -m unittest tests.test_calls
```

Los avisos de texto antiguos se mantienen como fallback visible, pero el runner no los
convierte en llamadas estructuradas.
