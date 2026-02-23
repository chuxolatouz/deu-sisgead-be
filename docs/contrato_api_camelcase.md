# Contrato API FE-BE (CamelCase)

## Estado

- Estándar final: `camelCase`.
- Compatibilidad temporal: el backend acepta `snake_case` en endpoints heredados por 1 release.

## Proyectos y Actividades

### Crear proyecto

- Endpoint: `POST /crear_proyecto`
- FE envía:
  - `nombre`, `descripcion`, `categoria`
  - `fechaInicio`, `fechaFin`
  - `objetivoGeneral`, `objetivosEspecificos`
  - opcional `departmentId`
- Backend persiste manteniendo los campos internos actuales y normaliza alias.

### Actualizar proyecto

- Endpoint: `PUT /actualizar_proyecto/:projectId`
- FE envía camelCase con el mismo criterio de creación.

### Asignar balance

- Endpoint: `PATCH /asignar_balance`
- FE envía:
  - `projectId`
  - `balance`
- Compatibilidad:
  - `project_id` / `proyecto_id` también son aceptados.

### Asignar/eliminar miembro de proyecto

- Endpoints:
  - `PATCH /asignar_usuario_proyecto`
  - `PATCH /eliminar_usuario_proyecto`
- FE envía:
  - asignar: `projectId`, `user`, `role`
  - eliminar: `projectId`, `userId`

### Reglas

- Endpoints:
  - `POST /asignar_regla_distribucion`
  - `POST /asignar_regla_fija/`
- FE envía:
  - distribución: `projectId`, `distributionRule`
  - regla fija: `projectId`, `ruleId`

### Actividades

- Crear actividad:
  - Endpoint: `POST /documento_crear` (multipart)
  - FE envía: `projectId`, `descripcion`, `monto`, `specificObjective`, `files`
- Finalizar actividad:
  - Endpoint: `POST /documento_cerrar` (multipart)
  - FE envía: `projectId`, `docId`, `monto`, `description`, `referencia`, `transferAmount`, `banco`, `accountCode`, `files`
- Eliminar actividad:
  - Endpoint: `POST /documento_eliminar` (alias: `/eliminar_presupuesto`)
  - FE envía: `projectId`, `budgetId`

## Departamentos

### Listado

- Endpoint: `GET /departamentos`
- Soporta:
  - sin paginación: respuesta tipo array (legacy)
  - con `page`/`limit`: respuesta `{ request_list, count }`

### Detalle

- Endpoint: `GET /departamentos/:departmentId`

### Nuevos endpoints usados por FE

- `GET /departamentos/:departmentId/proyectos?page=&limit=`
- `GET /departamentos/:departmentId/usuarios?page=&limit=`

## Usuarios/Auth

### Registro

- Endpoint: `POST /registrar`
- FE envía: `nombre`, `email`, `password`, `rol`

### Cambiar rol de usuario

- Endpoint: `POST /cambiar_rol_usuario`
- FE envía: `id`, `rol`
- Roles válidos:
  - `usuario`
  - `admin_departamento`
  - `super_admin`

### Editar usuario

- Endpoint: `PUT /editar_usuario/:idUsuario`
- FE envía objeto parcial (`nombre`, `password`, etc.).

## Contabilidad

### Búsqueda de cuentas

- `GET /api/accounts/search?year=2025&q=&group=&scopeType=&scopeId=`

### Cuentas por proyecto/departamento (árbol)

- `GET /api/projects/:projectId/accounts?year=2025&assignedOnly=true|false&includeZero=true|false`
- `GET /api/departments/:departmentId/accounts?year=2025&assignedOnly=true|false&includeZero=true|false`
- Defaults compatibles:
  - `assignedOnly=false`
  - `includeZero=true`
- Meta en respuesta:
  - `meta.assignedOnly`
  - `meta.includeZero`
  - `meta.totalAssigned`
  - `meta.totalVisible`
  - `meta.totalBalanceVisible`

### Transferencia entre cuentas

- `POST /api/admin/accounts/transfer`
- Campos:
  - `year`
  - `fromScopeType`, `fromScopeId`
  - `toScopeType`, `toScopeId`
  - `fromAccountCode`, `toAccountCode`
  - `amount`
  - opcionales: `description`, `fromAccountDescription`, `toAccountDescription`, `reference`

### Cargar saldo manual

- Global: `POST /api/admin/accounts/movements`
- Proyecto: `POST /api/projects/:projectId/movements?year=`
- Departamento: `POST /api/departments/:departmentId/movements?year=`
- Campos:
  - `accountCode`, `type`, `amount`
  - opcionales: `description`, `reference`

## Nota de migración

- Durante la ventana de compatibilidad, el backend acepta ambos contratos (`camelCase` y `snake_case`) en endpoints heredados.
- El frontend debe emitir únicamente `camelCase`.
