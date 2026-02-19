# Modulo de Cuentas Contables (2025)

## Resumen

Este modulo agrega un catalogo maestro de contabilidad 2025 y estados/movimientos por scope (`department` y `project`) sin duplicar fisicamente el catalogo completo en cada ambito.

Colecciones:
- `master_accounts`
- `master_units`
- `master_funding_sources`
- `master_budget_categories`
- `account_scope_state`
- `ledger_movements`

## Seed (idempotente)

### CLI

```bash
python -m scripts.seed_contabilidad_2025 --year 2025
python -m scripts.seed_contabilidad_2025 --year 2025 --force
python -m scripts.seed_contabilidad_2025 --year 2025 --dry-run
python -m scripts.seed_contabilidad_2025 --year 2025 --sync-departments
```

### Endpoint admin

```http
POST /api/admin/seed/contabilidad/2025?force=true
POST /admin/seed/contabilidad/2025?dry_run=true
```

Requiere `role=super_admin`.

### Fuentes de datos

Ruta preferida en repo:
- `data/contabilidad/2025/contabilidad_2025_accounts.csv`
- `data/contabilidad/2025/contabilidad_2025_unidades_ejecutoras.csv`
- `data/contabilidad/2025/contabilidad_2025_fuentes_financiamiento.csv`
- `data/contabilidad/2025/contabilidad_2025_categoria_presupuestaria.csv`
- `data/contabilidad/2025/contabilidad_2025_accounts.json` (se genera si no existe)

Fallback durante seed:
1. `~/Downloads/*.csv`
2. `TABLAS DE CONTABILIDAD AÑO 2025 (Sistema).xlsx` (en `data/...` o `~/Downloads`), regenerando CSV automaticamente.

## Endpoints

### Catalogo
- `GET /accounts/tree?year=2025&group=EGRESO`
- `GET /api/accounts/tree?year=2025&group=EGRESO`
- `GET /accounts/search?year=2025&q=texto&group=INGRESO`
- `GET /api/accounts/search?year=2025&q=texto&group=INGRESO`

### Scope Departamentos
- `GET /departments/:id/accounts?year=2025`
- `POST /departments/:id/accounts/init?year=2025&mode=detail_only|all|group:EGRESO`
- `POST /departments/:id/movements`

### Scope Proyectos
- `GET /projects/:id/accounts?year=2025`
- `POST /projects/:id/accounts/init?year=2025&mode=detail_only|all|group:EGRESO`
- `POST /projects/:id/movements`

### Admin
- `POST /api/admin/seed/contabilidad/2025`
- `POST /api/admin/sync/departments-from-units?year=2025`
- `GET /api/admin/contabilidad/consolidado?year=2025[&scopeType=department|project&scopeId=...]`
- `POST /api/admin/accounts/transfer`
- `POST /api/admin/accounts/movements` (scope `department|project|global`)

## Ejemplos de payload

### Crear movimiento (department/project)

```json
{
  "accountCode": "401010100000",
  "type": "debit",
  "amount": 1500,
  "description": "Compra de insumos",
  "reference": {
    "kind": "manual",
    "id": "MOV-0001"
  }
}
```

### Transferir entre cuentas (admin)

```json
{
  "year": 2025,
  "fromScopeType": "global",
  "fromScopeId": "global",
  "toScopeType": "department",
  "toScopeId": "67ab1234...",
  "fromAccountCode": "401010100000",
  "toAccountCode": "401010200000",
  "fromAccountDescription": "Servicios básicos",
  "toAccountDescription": "Materiales y suministros",
  "amount": 250,
  "description": "Transferencia interna"
}
```

## Reglas aplicadas

- Catalogo master es `source of truth`.
- `account_scope_state` es snapshot para performance.
- `ledger_movements` es historico de movimientos.
- Estrategia lazy por defecto: si no hay estado para una cuenta, balance asumido `0`.
- Inicializacion eager disponible via `/accounts/init`.
- Validacion configurable de negativos por env var:
  - `ACCOUNTING_ALLOW_NEGATIVE=true|false` (default `true`).

## RBAC aplicado

- `super_admin`: acceso total + seed/sync/consolidado.
- `admin_departamento` y `usuario`: solo su departamento (`departamento_id`) y proyectos asociados (por departamento, owner o miembro).

## Integracion con datos existentes

- En movimientos de proyecto, se registra log de auditoria usando `agregar_log`.
- No se duplica el catalogo en departamentos/proyectos.
