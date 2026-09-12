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

# List all tables so you can see what's really in BASICTEST
cursor.execute("""
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
""")
print("Tables found in BASICTEST:")
for row in cursor.fetchall():
    print(" -", row.TABLE_NAME)

conn.close()