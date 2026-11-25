from datetime import datetime
from decimal import Decimal

import pandas as pd
import requests
import streamlit as st


API = "http://127.0.0.1:8000"

STLPCE = {
    "id": "ID",
    "cas": "Dátum a čas",
    "suma": "Suma (€)",
    "kategoria": "Kategória",
    "obchodnik": "Obchodník",
}

st.set_page_config(page_title="Osobné financie", layout="wide")
st.title("Osobné financie")


def api(metoda, cesta, **kwargs):
    token = st.session_state.get("token")
    hlavicky = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        odpoved = requests.request(
            metoda, API + cesta, headers=hlavicky, timeout=60, **kwargs
        )
    except requests.RequestException:
        st.error("Server neodpovedá. Skontrolujte, či je spustený.")
        st.stop()

    if token and odpoved.status_code == 401:
        st.session_state.pop("token", None)
        st.session_state["oznam"] = "Prihláste sa znova."
        st.rerun()

    return odpoved


def zobraz_chybu(odpoved):
    try:
        detail = odpoved.json().get("detail")
    except ValueError:
        detail = None

    if isinstance(detail, list):
        detail = " | ".join(f"{p['pole']}: {p['dovod']}" for p in detail)

    st.error(detail or f"Požiadavka zlyhala (HTTP {odpoved.status_code}).")


def uspech(sprava):
    st.session_state["uspech"] = sprava
    st.rerun()


def prihlasovacia_stranka():
    stav = api("GET", "/nastavenie")
    if not stav.ok:
        zobraz_chybu(stav)
        return

    if stav.json()["vyzaduje_spravcu"]:
        st.subheader("Vytvorenie prvého správcu")
        with st.form("prvy_spravca"):
            meno = st.text_input("Používateľské meno")
            heslo = st.text_input("Heslo", type="password")
            potvrdenie = st.text_input("Zopakujte heslo", type="password")
            odoslat = st.form_submit_button("Vytvoriť účet správcu")

        if odoslat:
            if heslo != potvrdenie:
                st.error("Heslá sa nezhodujú.")
                return

            odpoved = api(
                "POST", "/nastavenie", json={"meno": meno.strip(), "heslo": heslo}
            )
            if odpoved.status_code == 201:
                uspech("Účet správcu bol vytvorený. Teraz sa prihláste.")
            zobraz_chybu(odpoved)
        return

    rezim = st.radio("Používateľský účet", ["Prihlásenie", "Registrácia"], horizontal=True)
    with st.form("prihlasenie"):
        meno = st.text_input("Používateľské meno")
        heslo = st.text_input("Heslo", type="password")
        odoslat = st.form_submit_button(
            "Prihlásiť sa" if rezim == "Prihlásenie" else "Registrovať sa"
        )

    if odoslat:
        cesta = "/prihlasenie" if rezim == "Prihlásenie" else "/registracia"
        odpoved = api("POST", cesta, json={"meno": meno.strip(), "heslo": heslo})

        if odpoved.status_code == 200:
            st.session_state["token"] = odpoved.json()["token"]
            st.rerun()
        elif odpoved.status_code == 201:
            st.success("Registrácia prebehla úspešne. Teraz sa môžete prihlásiť.")
        else:
            zobraz_chybu(odpoved)


def formular_transakcie(ucet_id, zaznam=None):
    cas = (
        datetime.fromisoformat(zaznam["cas"])
        if zaznam
        else datetime.now().replace(microsecond=0)
    )
    kluc = f"transakcia-{ucet_id}-{zaznam['id'] if zaznam else 'nova'}"

    with st.form(kluc):
        datum_cas = st.text_input(
            "Dátum a čas", value=cas.isoformat(sep=" ", timespec="seconds")
        )
        suma = st.text_input("Suma (€)", value=str(zaznam["suma"]) if zaznam else "")
        kategoria = st.text_input("Kategória", value=zaznam["kategoria"] if zaznam else "")
        obchodnik = st.text_input("Obchodník", value=zaznam["obchodnik"] if zaznam else "")

        if st.form_submit_button("Uložiť transakciu"):
            vstup = {
                "ucet_id": ucet_id,
                "cas": datum_cas.strip(),
                "suma": suma.strip().replace(",", "."),
                "kategoria": kategoria,
                "obchodnik": obchodnik,
            }
            if zaznam:
                odpoved = api("PUT", f"/transakcie/{zaznam['id']}", json=vstup)
            else:
                odpoved = api("POST", "/transakcie", json=vstup)

            if odpoved.ok:
                uspech("Transakcia bola uložená.")
            zobraz_chybu(odpoved)


def formular_importu():
    with st.expander("Import údajov"):
        typ = st.selectbox("Údaje", ["Účty", "Transakcie"])
        subor = st.file_uploader("Súbor CSV", type="csv", key=typ)

        if st.button("Importovať", disabled=subor is None):
            cesta = "/import/ucty" if typ == "Účty" else "/import/transakcie"
            odpoved = api(
                "POST",
                cesta,
                files={"subor": (subor.name, subor.getvalue(), "text/csv")},
            )

            if not odpoved.ok:
                zobraz_chybu(odpoved)
                return

            vysledok = odpoved.json()
            st.write(
                f"Vložené: {vysledok['vlozene']} · "
                f"Odmietnuté: {vysledok['odmietnute']}"
            )
            if vysledok["chyby"]:
                st.dataframe(
                    pd.DataFrame(vysledok["chyby"]).rename(
                        columns={"riadok": "Riadok", "dovod": "Dôvod"}
                    ),
                    hide_index=True,
                )


def zobraz_transakcie(ucet_id, zaznamy, admin):
    kategorie = sorted({z["kategoria"] for z in zaznamy})
    vyber = st.multiselect("Kategórie", kategorie, key=f"kategorie-{ucet_id}")
    zobrazene = [z for z in zaznamy if not vyber or z["kategoria"] in vyber]

    if zobrazene:
        data = pd.DataFrame(zobrazene)[list(STLPCE)].rename(columns=STLPCE)
        st.dataframe(data, hide_index=True)
    else:
        st.info("Žiadne transakcie.")

    if not admin:
        return

    with st.expander("Pridať transakciu"):
        formular_transakcie(ucet_id)

    if zobrazene:
        with st.expander("Upraviť alebo zmazať transakciu"):
            index = {z["id"]: z for z in zobrazene}
            identifikator = st.selectbox(
                "Transakcia",
                list(index),
                format_func=lambda i: f"#{i} · {index[i]['obchodnik']} · {index[i]['suma']} €",
            )
            formular_transakcie(ucet_id, index[identifikator])

            potvrdenie = st.checkbox(
                "Potvrdzujem zmazanie", key=f"zmazat-{identifikator}"
            )
            if st.button("Zmazať transakciu", disabled=not potvrdenie):
                odpoved = api("DELETE", f"/transakcie/{identifikator}")
                if odpoved.ok:
                    uspech("Transakcia bola zmazaná.")
                zobraz_chybu(odpoved)


def zobraz_prehlad(ucet_id, zaznamy):
    if not zaznamy:
        st.info("Na vytvorenie prehľadu potrebujete transakcie.")
        return

    data = pd.DataFrame(zaznamy)
    data["Mesiac"] = pd.to_datetime(data["cas"], format="ISO8601").dt.strftime("%Y-%m")
    centy = data["suma"].map(lambda suma: int(Decimal(str(suma)) * 100))
    data["Príjmy"] = centy.clip(lower=0)
    data["Výdavky"] = -centy.clip(upper=0)
    mesiace = data.groupby("Mesiac")[["Príjmy", "Výdavky"]].sum()

    st.subheader("Príjmy a výdavky podľa mesiacov")
    st.bar_chart(mesiace / 100, y_label="Suma (€)", stack=False)

    mesiac = st.selectbox(
        "Mesiac", list(reversed(mesiace.index)), key=f"mesiac-{ucet_id}"
    )
    saldo = mesiace.loc[mesiac, "Príjmy"] - mesiace.loc[mesiac, "Výdavky"]
    st.metric("Mesačné saldo", f"{saldo / 100:.2f} €")

    st.subheader("Výdavky podľa kategórií")
    vydavky = data[(data["Mesiac"] == mesiac) & (data["Výdavky"] > 0)]
    if vydavky.empty:
        st.info("V tomto mesiaci nie sú žiadne výdavky.")
    else:
        podla_kategorie = vydavky.groupby("kategoria")["Výdavky"].sum() / 100
        st.bar_chart(podla_kategorie.rename_axis("Kategória"), y_label="Suma (€)")


if "oznam" in st.session_state:
    st.warning(st.session_state.pop("oznam"))
if "uspech" in st.session_state:
    st.success(st.session_state.pop("uspech"))

if "token" not in st.session_state:
    prihlasovacia_stranka()
    st.stop()

odpoved = api("GET", "/ja")
if not odpoved.ok:
    zobraz_chybu(odpoved)
    st.stop()

pouzivatel = odpoved.json()
admin = pouzivatel["rola"] == "admin"
rola = "Správca" if admin else "Člen"
st.sidebar.write(f"{pouzivatel['meno']} · {rola}")

if st.sidebar.button("Odhlásiť sa"):
    odpoved = api("POST", "/odhlasenie")
    if odpoved.ok:
        st.session_state.clear()
        st.rerun()
    zobraz_chybu(odpoved)

if admin:
    formular_importu()

odpoved = api("GET", "/ucty")
if not odpoved.ok:
    zobraz_chybu(odpoved)
    st.stop()

ucty = {ucet["id"]: ucet for ucet in odpoved.json()}
if not ucty:
    st.info("Žiadne účty." if admin else "Nemáte pridelený žiadny účet.")
    st.stop()

ucet_id = st.selectbox(
    "Účet",
    list(ucty),
    format_func=lambda i: f"{ucty[i]['mesto']} · účet {i} · {ucty[i]['typ_uctu']}",
)

if admin:
    with st.expander("Prideliť prístup k účtu"):
        with st.form("pristup"):
            meno = st.text_input("Používateľské meno člena")
            if st.form_submit_button("Prideliť prístup"):
                odpoved = api("PUT", f"/ucty/{ucet_id}/pristupy", json={"meno": meno.strip()})
                if odpoved.ok:
                    st.success("Prístup bol pridelený.")
                else:
                    zobraz_chybu(odpoved)

odpoved = api("GET", f"/ucty/{ucet_id}/transakcie")
if not odpoved.ok:
    zobraz_chybu(odpoved)
    st.stop()

zaznamy = odpoved.json()
tabulka, prehlad = st.tabs(["Transakcie", "Mesačný prehľad"])

with tabulka:
    zobraz_transakcie(ucet_id, zaznamy, admin)

with prehlad:
    zobraz_prehlad(ucet_id, zaznamy)