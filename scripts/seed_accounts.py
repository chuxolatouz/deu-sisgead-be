"""
Script para cargar cuentas contables iniciales en la base de datos.
Ejecutar desde la raíz del proyecto con: python scripts/seed_accounts.py
"""

import sys
import os
from datetime import datetime

# Agregar el directorio raíz al path para poder importar módulos
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api import create_app
from api.extensions import mongo


def crear_indices():
    """Crear índices necesarios para la colección de cuentas"""
    print("Creando índices para la colección 'accounts'...")
    
    try:
        # Índice único en el código de cuenta
        mongo.db.accounts.create_index("code", unique=True, name="idx_code_unique")
        print("✓ Índice único en 'code' creado")
        
        # Índice en el campo active
        mongo.db.accounts.create_index("active", name="idx_active")
        print("✓ Índice en 'active' creado")
        
        # Índice compuesto en code y active
        mongo.db.accounts.create_index([("code", 1), ("active", 1)], name="idx_code_active")
        print("✓ Índice compuesto en 'code' y 'active' creado")
        
        # Índice de texto en name para búsquedas
        mongo.db.accounts.create_index([("name", "text")], name="idx_name_text")
        print("✓ Índice de texto en 'name' creado")
        
        print("\n✅ Todos los índices creados exitosamente\n")
        
    except Exception as e:
        print(f"❌ Error al crear índices: {str(e)}")


def seed_cuentas():
    """Cargar cuentas contables iniciales"""
    print("Iniciando seed de cuentas contables...")
    
    # Catálogo de cuentas contables típicas (Plan Contable General)
    cuentas_iniciales = [
        # 1. ACTIVOS
        {"code": "1", "name": "ACTIVOS", "description": "Recursos controlados por la entidad", "active": True},
        {"code": "1.1", "name": "ACTIVO CORRIENTE", "description": "Activos realizables en el corto plazo", "active": True},
        {"code": "1.1.01", "name": "Caja", "description": "Efectivo en caja", "active": True},
        {"code": "1.1.02", "name": "Bancos", "description": "Depósitos en instituciones financieras", "active": True},
        {"code": "1.1.03", "name": "Inversiones Temporales", "description": "Inversiones a corto plazo", "active": True},
        {"code": "1.1.04", "name": "Cuentas por Cobrar", "description": "Derechos de cobro a corto plazo", "active": True},
        {"code": "1.1.05", "name": "Inventarios", "description": "Bienes destinados a la venta o consumo", "active": True},
        {"code": "1.1.06", "name": "Gastos Pagados por Anticipado", "description": "Pagos anticipados de gastos", "active": True},
        
        {"code": "1.2", "name": "ACTIVO NO CORRIENTE", "description": "Activos de largo plazo", "active": True},
        {"code": "1.2.01", "name": "Propiedad, Planta y Equipo", "description": "Activos fijos tangibles", "active": True},
        {"code": "1.2.02", "name": "Depreciación Acumulada", "description": "Depreciación de activos fijos", "active": True},
        {"code": "1.2.03", "name": "Inversiones a Largo Plazo", "description": "Inversiones permanentes", "active": True},
        {"code": "1.2.04", "name": "Activos Intangibles", "description": "Activos sin sustancia física", "active": True},
        
        # 2. PASIVOS
        {"code": "2", "name": "PASIVOS", "description": "Obligaciones presentes de la entidad", "active": True},
        {"code": "2.1", "name": "PASIVO CORRIENTE", "description": "Obligaciones a corto plazo", "active": True},
        {"code": "2.1.01", "name": "Cuentas por Pagar", "description": "Obligaciones de pago a proveedores", "active": True},
        {"code": "2.1.02", "name": "Préstamos Bancarios a Corto Plazo", "description": "Deudas bancarias menores a un año", "active": True},
        {"code": "2.1.03", "name": "Impuestos por Pagar", "description": "Obligaciones tributarias pendientes", "active": True},
        {"code": "2.1.04", "name": "Sueldos y Salarios por Pagar", "description": "Remuneraciones pendientes de pago", "active": True},
        {"code": "2.1.05", "name": "Provisiones a Corto Plazo", "description": "Provisiones para obligaciones futuras", "active": True},
        
        {"code": "2.2", "name": "PASIVO NO CORRIENTE", "description": "Obligaciones a largo plazo", "active": True},
        {"code": "2.2.01", "name": "Préstamos Bancarios a Largo Plazo", "description": "Deudas bancarias mayores a un año", "active": True},
        {"code": "2.2.02", "name": "Obligaciones por Beneficios a Empleados", "description": "Provisiones laborales a largo plazo", "active": True},
        {"code": "2.2.03", "name": "Pasivos por Arrendamiento", "description": "Obligaciones por arrendamientos financieros", "active": True},
        
        # 3. PATRIMONIO
        {"code": "3", "name": "PATRIMONIO", "description": "Capital y resultados acumulados", "active": True},
        {"code": "3.1", "name": "Capital Social", "description": "Aporte de los propietarios", "active": True},
        {"code": "3.2", "name": "Reservas", "description": "Utilidades retenidas por disposición legal o estatutaria", "active": True},
        {"code": "3.3", "name": "Resultados Acumulados", "description": "Utilidades o pérdidas de ejercicios anteriores", "active": True},
        {"code": "3.4", "name": "Resultado del Ejercicio", "description": "Utilidad o pérdida del período actual", "active": True},
        
        # 4. INGRESOS
        {"code": "4", "name": "INGRESOS", "description": "Incrementos en beneficios económicos", "active": True},
        {"code": "4.1", "name": "INGRESOS OPERACIONALES", "description": "Ingresos por la actividad principal", "active": True},
        {"code": "4.1.01", "name": "Ventas de Bienes", "description": "Ingresos por venta de productos", "active": True},
        {"code": "4.1.02", "name": "Prestación de Servicios", "description": "Ingresos por servicios prestados", "active": True},
        {"code": "4.1.03", "name": "Transferencias Corrientes", "description": "Transferencias recibidas del gobierno u otros", "active": True},
        
        {"code": "4.2", "name": "INGRESOS NO OPERACIONALES", "description": "Ingresos por actividades secundarias", "active": True},
        {"code": "4.2.01", "name": "Ingresos Financieros", "description": "Intereses y rendimientos financieros", "active": True},
        {"code": "4.2.02", "name": "Otros Ingresos", "description": "Ingresos diversos no operacionales", "active": True},
        
        # 5. GASTOS
        {"code": "5", "name": "GASTOS", "description": "Decrementos en beneficios económicos", "active": True},
        {"code": "5.1", "name": "GASTOS OPERACIONALES", "description": "Gastos relacionados con la operación", "active": True},
        {"code": "5.1.01", "name": "Costo de Ventas", "description": "Costo de los bienes o servicios vendidos", "active": True},
        {"code": "5.1.02", "name": "Gastos de Personal", "description": "Sueldos, salarios y beneficios sociales", "active": True},
        {"code": "5.1.03", "name": "Servicios Básicos", "description": "Agua, luz, teléfono, internet", "active": True},
        {"code": "5.1.04", "name": "Arrendamientos", "description": "Alquileres de inmuebles y equipos", "active": True},
        {"code": "5.1.05", "name": "Depreciación", "description": "Depreciación de activos fijos", "active": True},
        {"code": "5.1.06", "name": "Materiales y Suministros", "description": "Materiales de oficina y operación", "active": True},
        {"code": "5.1.07", "name": "Mantenimiento y Reparaciones", "description": "Gastos de mantenimiento", "active": True},
        
        {"code": "5.2", "name": "GASTOS NO OPERACIONALES", "description": "Gastos no relacionados con la operación principal", "active": True},
        {"code": "5.2.01", "name": "Gastos Financieros", "description": "Intereses y comisiones bancarias", "active": True},
        {"code": "5.2.02", "name": "Pérdida en Venta de Activos", "description": "Pérdidas por venta de activos fijos", "active": True},
        {"code": "5.2.03", "name": "Otros Gastos", "description": "Gastos diversos no operacionales", "active": True},
    ]
    
    # Verificar si ya existen cuentas
    cuenta_existente = mongo.db.accounts.count_documents({})
    
    if cuenta_existente > 0:
        print(f"⚠️  Ya existen {cuenta_existente} cuentas en la base de datos.")
        respuesta = input("¿Desea eliminar las cuentas existentes y recargar? (s/n): ")
        
        if respuesta.lower() == 's':
            mongo.db.accounts.delete_many({})
            print("✓ Cuentas existentes eliminadas")
        else:
            print("❌ Operación cancelada")
            return
    
    # Insertar cuentas
    print(f"\nInsertando {len(cuentas_iniciales)} cuentas contables...")
    
    try:
        for cuenta in cuentas_iniciales:
            cuenta["created_at"] = datetime.utcnow()
            cuenta["updated_at"] = datetime.utcnow()
            cuenta["created_by"] = "system"
        
        resultado = mongo.db.accounts.insert_many(cuentas_iniciales)
        
        print(f"✅ Se insertaron {len(resultado.inserted_ids)} cuentas exitosamente\n")
        
        # Mostrar resumen por categoría
        print("📊 Resumen por categoría:")
        print(f"   • Activos: {len([c for c in cuentas_iniciales if c['code'].startswith('1')])}")
        print(f"   • Pasivos: {len([c for c in cuentas_iniciales if c['code'].startswith('2')])}")
        print(f"   • Patrimonio: {len([c for c in cuentas_iniciales if c['code'].startswith('3')])}")
        print(f"   • Ingresos: {len([c for c in cuentas_iniciales if c['code'].startswith('4')])}")
        print(f"   • Gastos: {len([c for c in cuentas_iniciales if c['code'].startswith('5')])}")
        print()
        
    except Exception as e:
        print(f"❌ Error al insertar cuentas: {str(e)}")


def main():
    """Función principal"""
    print("=" * 60)
    print("  SEED DE CUENTAS CONTABLES - DEU SISGEAD")
    print("=" * 60)
    print()
    
    # Crear aplicación Flask
    app = create_app()
    
    with app.app_context():
        # Crear índices
        crear_indices()
        
        # Seed de cuentas
        seed_cuentas()
        
        print("=" * 60)
        print("  ✅ PROCESO COMPLETADO")
        print("=" * 60)


if __name__ == "__main__":
    main()
