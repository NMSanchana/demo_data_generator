import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=217.217.250.150;"
    "DATABASE=BASICTEST;"
    "UID=qc;"
    "PWD=goodbooksqc@123;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

# Edit this list to whichever tables you want to inspect.
# Start with ones that look like they'd back a foreign-key field,
# e.g. MDEPARTMENT for a DepartmentId field, MCOSTCENTER for a
# CostCenterId field, MEMPLOYEE for an EmployeeId field, etc.
TABLES_TO_INSPECT = [
    "MDEPARTMENT",
    "MCOSTCENTER",
    "MITEMGROUP",
]

for table in TABLES_TO_INSPECT:
    print(f"\n=== Columns in {table} ===")
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, table)
    columns = cursor.fetchall()
    if not columns:
        print("  (table not found or has no columns -- check the name)")
        continue
    for col in columns:
        print(f"  {col.COLUMN_NAME:<30} {col.DATA_TYPE:<15} nullable={col.IS_NULLABLE}")

    # Also show a few real sample rows so you can see actual id/label values
    print(f"\n  Sample rows from {table}:")
    try:
        cursor.execute(f"SELECT TOP 3 * FROM {table}")
        col_names = [desc[0] for desc in cursor.description]
        print("  ", col_names)
        for row in cursor.fetchall():
            print("  ", list(row))
    except Exception as e:
        print(f"  Could not fetch sample rows: {e}")

conn.close()