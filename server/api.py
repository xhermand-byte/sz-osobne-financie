import csv
from contextlib import asynccontextmanager
from io import StringIO

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from server import auth, db
from server.modely import Pristup, Transakcia, TransakciaCSV, UcetCSV, sprava_validacie


@asynccontextmanager
async def zivotny_cyklus(app):
    db.inicializuj()
    priprav_vzorove_data()
    yield


app = FastAPI(title="Osobné financie", lifespan=zivotny_cyklus)
app.include_router(auth.router)

prihlaseny = Depends(auth.aktualny_pouzivatel)
spravca = Depends(auth.vyzaduj_admina)


@app.exception_handler(RequestValidationError)
async def neplatny_vstup(request: Request, chyba: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "pole": ".".join(map(str, polozka["loc"])),
                    "dovod": sprava_validacie(polozka),
                }
                for polozka in chyba.errors()
            ]
        },
    )


def over_ucet(con, ucet_id, pouzivatel):
    if con.execute("SELECT id FROM ucty WHERE id = ?", [ucet_id]).fetchone() is None:
        raise HTTPException(404, "Účet sa nenašiel.")

    if pouzivatel["rola"] != "admin":
        pristup = con.execute(
            "SELECT 1 FROM pristupy WHERE pouzivatel_id = ? AND ucet_id = ?",
            [pouzivatel["id"], ucet_id],
        ).fetchone()

        if pristup is None:
            raise HTTPException(403, "K tomuto účtu nemáte prístup.")


def najdi_transakciu(con, transakcia_id):
    riadky = db.zoznam(con, "SELECT * FROM transakcie WHERE id = ?", [transakcia_id])
    if not riadky:
        raise HTTPException(404, "Transakcia sa nenašla.")
    return riadky[0]


def hodnoty_transakcie(vstup):
    return [vstup.ucet_id, vstup.cas, vstup.suma, vstup.kategoria, vstup.obchodnik]


@app.get("/ucty", summary="Zoznam dostupných účtov")
def ucty(pouzivatel=prihlaseny):
    with db.pripojenie() as con:
        return db.zoznam(
            con,
            """
            SELECT u.* FROM ucty AS u
            WHERE ? = 'admin'
               OR EXISTS (
                   SELECT 1 FROM pristupy AS p
                   WHERE p.ucet_id = u.id AND p.pouzivatel_id = ?
               )
            ORDER BY u.id
            """,
            [pouzivatel["rola"], pouzivatel["id"]],
        )


@app.put("/ucty/{ucet_id}/pristupy", status_code=204, summary="Pridelenie prístupu")
def pridel_pristup(ucet_id: int, vstup: Pristup, pouzivatel=spravca):
    with db.ZAPIS, db.pripojenie() as con:
        over_ucet(con, ucet_id, pouzivatel)
        clen = con.execute(
            "SELECT id FROM pouzivatelia WHERE meno = ?", [vstup.meno]
        ).fetchone()

        if clen is None:
            raise HTTPException(404, "Používateľ sa nenašiel.")

        con.execute(
            """
            INSERT INTO pristupy (pouzivatel_id, ucet_id)
            VALUES (?, ?) ON CONFLICT DO NOTHING
            """,
            [clen[0], ucet_id],
        )

    return Response(status_code=204)


@app.get("/ucty/{ucet_id}/transakcie", summary="Zoznam transakcií účtu")
def transakcie_uctu(
    ucet_id: int,
    kategoria: str | None = None,
    pouzivatel=prihlaseny,
):
    with db.pripojenie() as con:
        over_ucet(con, ucet_id, pouzivatel)
        return db.zoznam(
            con,
            """
            SELECT * FROM transakcie
            WHERE ucet_id = ? AND (? IS NULL OR kategoria = ?)
            ORDER BY cas DESC, id DESC
            """,
            [ucet_id, kategoria, kategoria],
        )


@app.get("/transakcie/{transakcia_id}", summary="Detail transakcie")
def detail_transakcie(transakcia_id: int, pouzivatel=prihlaseny):
    with db.pripojenie() as con:
        zaznam = najdi_transakciu(con, transakcia_id)
        over_ucet(con, zaznam["ucet_id"], pouzivatel)
        return zaznam


@app.post("/transakcie", status_code=201, summary="Pridanie transakcie")
def pridaj_transakciu(vstup: Transakcia, pouzivatel=spravca):
    with db.ZAPIS, db.pripojenie() as con:
        over_ucet(con, vstup.ucet_id, pouzivatel)
        return db.zoznam(
            con,
            """
            INSERT INTO transakcie (id, ucet_id, cas, suma, kategoria, obchodnik)
            SELECT COALESCE(MAX(id), 0) + 1, ?, ?, ?, ?, ? FROM transakcie
            RETURNING *
            """,
            hodnoty_transakcie(vstup),
        )[0]


@app.put("/transakcie/{transakcia_id}", summary="Úprava transakcie")
def uprav_transakciu(transakcia_id: int, vstup: Transakcia, pouzivatel=spravca):
    with db.ZAPIS, db.pripojenie() as con:
        najdi_transakciu(con, transakcia_id)
        over_ucet(con, vstup.ucet_id, pouzivatel)

        return db.zoznam(
            con,
            """
            UPDATE transakcie
            SET ucet_id = ?, cas = ?, suma = ?, kategoria = ?, obchodnik = ?
            WHERE id = ? RETURNING *
            """,
            [*hodnoty_transakcie(vstup), transakcia_id],
        )[0]


@app.delete("/transakcie/{transakcia_id}", status_code=204, summary="Zmazanie transakcie")
def zmaz_transakciu(transakcia_id: int, pouzivatel=spravca):
    with db.ZAPIS, db.pripojenie() as con:
        zaznam = con.execute(
            "DELETE FROM transakcie WHERE id = ? RETURNING id",
            [transakcia_id],
        ).fetchone()

    if zaznam is None:
        raise HTTPException(404, "Transakcia sa nenašla.")

    return Response(status_code=204)


def nacitaj_csv(obsah, model):
    try:
        text = obsah.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Súbor CSV musí byť v kódovaní UTF-8.") from exc

    citacka = csv.DictReader(StringIO(text, newline=""), strict=True)
    try:
        if sorted(citacka.fieldnames or []) != sorted(model.model_fields):
            raise HTTPException(
                422, "Očakávané stĺpce CSV: " + ", ".join(model.model_fields)
            )
        return [(citacka.line_num, riadok) for riadok in citacka]
    except csv.Error as exc:
        raise HTTPException(
            422, f"Neplatný formát CSV pri riadku {citacka.line_num}."
        ) from exc


def importuj(con, obsah, model, preskoc_existujuce=False):
    riadky = nacitaj_csv(obsah, model)
    su_transakcie = model is TransakciaCSV
    platne = []
    chyby = []
    videne_id = set()

    ucty = {r[0] for r in con.execute("SELECT id FROM ucty").fetchall()}
    existujuce = ucty
    if su_transakcie:
        existujuce = {
            r[0] for r in con.execute("SELECT id FROM transakcie").fetchall()
        }

    for cislo, riadok in riadky:
        try:
            if None in riadok:
                raise ValueError("Riadok obsahuje nadbytočné hodnoty.")

            zaznam = model.model_validate(riadok)
            if zaznam.id in videne_id:
                raise ValueError(f"Záznam s ID {zaznam.id} sa v súbore opakuje.")
            videne_id.add(zaznam.id)

            if zaznam.id in existujuce:
                if preskoc_existujuce:
                    continue
                raise ValueError(f"Záznam s ID {zaznam.id} už existuje.")

            if su_transakcie and zaznam.ucet_id not in ucty:
                raise ValueError(f"Účet s ID {zaznam.ucet_id} neexistuje.")

        except ValidationError as exc:
            dovod = " | ".join(
                f"{'.'.join(map(str, chyba['loc']))}: {sprava_validacie(chyba)}"
                for chyba in exc.errors()
            )
            chyby.append({"riadok": cislo, "dovod": dovod})
            continue
        except ValueError as exc:
            chyby.append({"riadok": cislo, "dovod": str(exc)})
            continue

        if su_transakcie:
            platne.append([zaznam.id, *hodnoty_transakcie(zaznam)])
        else:
            platne.append([zaznam.id, zaznam.mesto, zaznam.typ_uctu])

    if platne:
        stlpce = [list(stlpec) for stlpec in zip(*platne)]
        if su_transakcie:
            con.execute(
                """
                INSERT INTO transakcie (id, ucet_id, cas, suma, kategoria, obchodnik)
                SELECT unnest(?::BIGINT[]), unnest(?::INTEGER[]),
                       unnest(?::TIMESTAMP[]), unnest(?::DECIMAL(14, 2)[]),
                       unnest(?::VARCHAR[]), unnest(?::VARCHAR[])
                """,
                stlpce,
            )
        else:
            con.execute(
                """
                INSERT INTO ucty (id, mesto, typ_uctu)
                SELECT unnest(?::INTEGER[]), unnest(?::VARCHAR[]),
                       unnest(?::VARCHAR[])
                """,
                stlpce,
            )

    return {"vlozene": len(platne), "odmietnute": len(chyby), "chyby": chyby}


def priprav_vzorove_data():
    with db.ZAPIS, db.pripojenie() as con:
        hotovo = con.execute(
            "SELECT 1 FROM inicializacia WHERE nazov = ?",
            ["vzorove_data"],
        ).fetchone()
        if hotovo:
            return

        con.begin()
        try:
            for nazov, model in (
                ("ucty.csv", UcetCSV),
                ("transakcie.csv", TransakciaCSV),
            ):
                cesta = db.DATA / nazov
                if not cesta.is_file():
                    raise RuntimeError(f"Chýba súbor data/{nazov}.")

                vysledok = importuj(
                    con, cesta.read_bytes(), model, preskoc_existujuce=True
                )
                if vysledok["chyby"]:
                    chyba = vysledok["chyby"][0]
                    raise RuntimeError(
                        f"Import {nazov}, riadok {chyba['riadok']}: {chyba['dovod']}"
                    )

            con.execute(
                "INSERT INTO inicializacia (nazov) VALUES (?)",
                ["vzorove_data"],
            )
            con.commit()
        except Exception:
            con.rollback()
            raise


@app.post("/import/ucty", summary="Import účtov zo súboru CSV")
def import_uctov(subor: UploadFile, pouzivatel=spravca):
    obsah = subor.file.read()
    with db.ZAPIS, db.pripojenie() as con:
        return importuj(con, obsah, UcetCSV)


@app.post("/import/transakcie", summary="Import transakcií zo súboru CSV")
def import_transakcii(subor: UploadFile, pouzivatel=spravca):
    obsah = subor.file.read()
    with db.ZAPIS, db.pripojenie() as con:
        return importuj(con, obsah, TransakciaCSV)