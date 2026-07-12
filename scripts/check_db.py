"""Check database state from agentboard containers."""
import os
os.environ["AGENTBOARD_DB_URL"] = "mysql+pymysql://agentboard:agentboard@db:3306/agentboard"
os.environ["AGENTBOARD_SECRET"] = "dev-insecure-secret-change-me-change-me!"

from agentboard.database import engine
from sqlalchemy import text

def main():
    with engine.connect() as c:
        tables = c.execute(text("SHOW TABLES")).fetchall()
        print("Tables:", [t[0] for t in tables])
        try:
            ver = c.execute(text("SELECT * FROM alembic_version")).fetchall()
            print("Alembic version:", [v[0] for v in ver])
        except Exception as e:
            print(f"Alembic version error: {e}")

if __name__ == "__main__":
    main()
