import os
from pathlib import Path
from threading import Lock

import duckdb


DATA = Path(__file__).resolve().parents[1] / "data"
DATABAZA = Path(os.environ.get("FINANCIE_DB", str(DATA / "financie.duckdb")))
ZAPIS = Lock()


def pripojenie():
    return duckdb.connect(str(DATABAZA))


def zoznam(con, sql, parametre=()):
    dotaz = con.execute(sql, parametre)
    stlpce = [stlpec[0] for stlpec in dotaz.description]
    return [dict(zip(stlpce, riadok)) for riadok in dotaz.fetchall()]


def inicializuj():
    DATABAZA.parent.mkdir(parents=True, exist_ok=True)

    with pripojenie() as con:
        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS pouzivatel_id START 1;

            CREATE TABLE IF NOT EXISTS pouzivatelia (
                id INTEGER PRIMARY KEY DEFAULT nextval('pouzivatel_id'),
                meno VARCHAR UNIQUE NOT NULL,
                heslo_hash VARCHAR NOT NULL,
                rola VARCHAR NOT NULL CHECK (rola IN ('admin', 'clen'))
            );

            CREATE TABLE IF NOT EXISTS relacie (
                token_hash VARCHAR PRIMARY KEY,
                pouzivatel_id INTEGER NOT NULL REFERENCES pouzivatelia(id),
                platnost TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ucty (
                id INTEGER PRIMARY KEY,
                mesto VARCHAR NOT NULL,
                typ_uctu VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pristupy (
                pouzivatel_id INTEGER REFERENCES pouzivatelia(id),
                ucet_id INTEGER REFERENCES ucty(id),
                PRIMARY KEY (pouzivatel_id, ucet_id)
            );

            CREATE TABLE IF NOT EXISTS transakcie (
                id BIGINT PRIMARY KEY,
                ucet_id INTEGER NOT NULL REFERENCES ucty(id),
                cas TIMESTAMP NOT NULL,
                suma DECIMAL(14, 2) NOT NULL,
                kategoria VARCHAR NOT NULL,
                obchodnik VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inicializacia (
                nazov VARCHAR PRIMARY KEY
            );
        """)