"""Migração única: SQLite local → PostgreSQL Render"""
import os, sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base, Usuario, Estoque, Pedido, CodigoRecuperacao, LogBusca
from models import Farmacia, Lead, Configuracao, PushSub, AlarmeRemedio

SQLITE = "sqlite:///./dosemed.db"
PG     = sys.argv[1] if len(sys.argv) > 1 else ""

if not PG:
    print("Uso: python migrar.py <postgresql_url>")
    sys.exit(1)

if PG.startswith("postgres://"):
    PG = PG.replace("postgres://", "postgresql://", 1)

print("Conectando ao SQLite local...")
src = create_engine(SQLITE, connect_args={"check_same_thread": False})
SrcSession = sessionmaker(bind=src)
s = SrcSession()

print("Conectando ao PostgreSQL (Render)...")
dst = create_engine(PG)
DstSession = sessionmaker(bind=dst)
d = DstSession()

print("Criando tabelas no PostgreSQL...")
Base.metadata.create_all(bind=dst)

def migrar(modelo, nome):
    rows = s.query(modelo).all()
    if not rows:
        print(f"  {nome}: vazio, pulando.")
        return
    d.query(modelo).delete()
    for row in rows:
        d.merge(row)
    d.commit()
    print(f"  {nome}: {len(rows)} registro(s) migrado(s).")

print("\nMigrando tabelas:")
migrar(Usuario,           "usuarios")
migrar(Estoque,           "estoque")
migrar(Pedido,            "pedidos")
migrar(CodigoRecuperacao, "codigos_recuperacao")
migrar(LogBusca,          "log_buscas")
migrar(Farmacia,          "farmacias")
migrar(Lead,              "leads")
migrar(Configuracao,      "configuracoes")
migrar(PushSub,           "push_subs")
migrar(AlarmeRemedio,     "alarmes")

print("\nMigração concluída!")
s.close()
d.close()
