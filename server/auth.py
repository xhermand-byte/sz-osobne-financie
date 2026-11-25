from hashlib import sha256
from secrets import token_urlsafe

import bcrypt
import duckdb
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server import db
from server.modely import Prihlasenie


router = APIRouter(tags=["Používateľské účty"])
bearer = HTTPBearer(auto_error=False, description="Token získaný po prihlásení.")


def hash_tokenu(token):
    return sha256(token.encode("utf-8")).hexdigest()


def hash_hesla(heslo):
    return bcrypt.hashpw(heslo.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def neprihlaseny(sprava):
    return HTTPException(401, sprava, headers={"WWW-Authenticate": "Bearer"})


def aktualny_pouzivatel(
    hlavicka: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    if hlavicka is None:
        raise neprihlaseny("Prihláste sa.")

    with db.pripojenie() as con:
        riadky = db.zoznam(
            con,
            """
            SELECT p.id, p.meno, p.rola
            FROM relacie AS r
            JOIN pouzivatelia AS p ON p.id = r.pouzivatel_id
            WHERE r.token_hash = ? AND r.platnost > now()
            """,
            [hash_tokenu(hlavicka.credentials)],
        )

    if not riadky:
        raise neprihlaseny("Token je neplatný alebo jeho platnosť vypršala.")

    return riadky[0]


def vyzaduj_admina(pouzivatel=Depends(aktualny_pouzivatel)):
    if pouzivatel["rola"] != "admin":
        raise HTTPException(403, "Táto operácia vyžaduje oprávnenia správcu.")
    return pouzivatel


def existuje_spravca(con):
    return con.execute(
        "SELECT 1 FROM pouzivatelia WHERE rola = ? LIMIT 1",
        ["admin"],
    ).fetchone() is not None


def over_miestnu_poziadavku(request):
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Úvodné nastavenie je dostupné iba lokálne.")


def vloz_pouzivatela(con, meno, heslo_hash, rola):
    try:
        return db.zoznam(
            con,
            """
            INSERT INTO pouzivatelia (meno, heslo_hash, rola)
            VALUES (?, ?, ?) RETURNING id, meno, rola
            """,
            [meno, heslo_hash, rola],
        )[0]
    except duckdb.ConstraintException as exc:
        raise HTTPException(409, "Používateľské meno je už obsadené.") from exc


@router.get("/nastavenie", summary="Stav úvodného nastavenia")
def stav_nastavenia(request: Request):
    over_miestnu_poziadavku(request)
    with db.pripojenie() as con:
        return {"vyzaduje_spravcu": not existuje_spravca(con)}


@router.post("/nastavenie", status_code=201, summary="Vytvorenie prvého správcu")
def vytvor_prveho_spravcu(request: Request, vstup: Prihlasenie):
    over_miestnu_poziadavku(request)

    with db.ZAPIS, db.pripojenie() as con:
        if existuje_spravca(con):
            raise HTTPException(409, "Úvodné nastavenie už bolo dokončené.")
        return vloz_pouzivatela(con, vstup.meno, hash_hesla(vstup.heslo), "admin")


@router.post("/registracia", status_code=201, summary="Registrácia používateľa")
def registracia(vstup: Prihlasenie):
    heslo_hash = hash_hesla(vstup.heslo)
    with db.ZAPIS, db.pripojenie() as con:
        return vloz_pouzivatela(con, vstup.meno, heslo_hash, "clen")


@router.post("/prihlasenie", summary="Prihlásenie")
def prihlasenie(vstup: Prihlasenie):
    with db.pripojenie() as con:
        riadky = db.zoznam(
            con,
            "SELECT id, heslo_hash FROM pouzivatelia WHERE meno = ?",
            [vstup.meno],
        )

    if not riadky or not bcrypt.checkpw(
        vstup.heslo.encode("utf-8"), riadky[0]["heslo_hash"].encode("ascii")
    ):
        raise neprihlaseny("Nesprávne meno alebo heslo.")

    token = token_urlsafe(32)

    with db.ZAPIS, db.pripojenie() as con:
        con.execute("DELETE FROM relacie WHERE platnost <= now()")
        con.execute(
            """
            INSERT INTO relacie (token_hash, pouzivatel_id, platnost)
            VALUES (?, ?, now() + INTERVAL '8 hours')
            """,
            [hash_tokenu(token), riadky[0]["id"]],
        )

    return {"token": token}


@router.post("/odhlasenie", status_code=204, summary="Odhlásenie")
def odhlasenie(
    hlavicka=Depends(bearer),
    pouzivatel=Depends(aktualny_pouzivatel),
):
    with db.ZAPIS, db.pripojenie() as con:
        con.execute(
            "DELETE FROM relacie WHERE token_hash = ?",
            [hash_tokenu(hlavicka.credentials)],
        )

    return Response(status_code=204)


@router.get("/ja", summary="Prihlásený používateľ")
def ja(pouzivatel=Depends(aktualny_pouzivatel)):
    return pouzivatel