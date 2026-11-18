# Architecture Decision Records (ADR)

Este directorio registra las decisiones de arquitectura relevantes del proyecto. Un ADR captura **por qué** se eligió algo, qué alternativas se descartaron y qué consecuencias aceptamos, para poder defender o revisar la decisión más tarde.

## Cuándo escribir un ADR

- La decisión afecta a más de un módulo, a un contrato (API, MCP, base de datos) o a una garantía del PRD.
- Había alternativas razonables y elegir una tiene costes.
- Cambiar la decisión más tarde sería caro.

Detalles locales de implementación no necesitan ADR.

## Formato

Archivo: `NNNN-titulo-en-kebab-case.md`, con `NNNN` correlativo (`0001`, `0002`, …). Los números no se reutilizan.

```markdown
# NNNN. Título

- **Estado:** Propuesto | Aceptado | Sustituido por NNNN | Descartado
- **Fecha:** AAAA-MM-DD
- **Historias relacionadas:** US-XX

## Contexto
Problema, fuerzas en juego y requisitos (citar el PRD / contrato).

## Opciones evaluadas
Cada alternativa con pros y contras.

## Decisión
Qué se elige y por qué.

## Consecuencias
Positivas, negativas, riesgos y qué hay que hacer/probar a raíz de esto.
```

## Reglas

- Un ADR aceptado es inmutable salvo correcciones menores. Para cambiar la decisión se crea un ADR nuevo y se marca el anterior como "Sustituido por NNNN".
- Cada ADR debe indicar cómo se verifica (tests que prueban la garantía).

## Índice

| Nº | Título | Estado |
| --- | --- | --- |
| [0001](0001-concurrencia-por-version.md) | Concurrencia en edición de viajes por versión optimista | Aceptado |
| [0002](0002-mock-proveedor-con-control.md) | Mock del proveedor con fixtures base, controles fuera del contrato y reloj controlable | Aceptado |
| [0003](0003-estados-de-reserva-y-reconciliacion.md) | Estados locales de reserva y reconciliación | Aceptado |
| [0004](0004-copia-de-oferta-y-frescura.md) | Copia de la oferta en el ítem y política de frescura | Aceptado |
| [0005](0005-jobs-documentos-y-canal-de-estado.md) | Jobs en PostgreSQL, documento como copia inmutable y canal de estado | Aceptado |
| [0006](0006-mcp-asistente-y-proveedor-de-modelo.md) | MCP en el mismo proceso, identidad por sesión, bucle propio y proveedor de modelo intercambiable | Aceptado |
| [0007](0007-frontend-mismo-origen-y-estado-en-vivo.md) | Frontend en el mismo origen, SSE leído con fetch y la UI que no es barrera de permisos | Aceptado |
