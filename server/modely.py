from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, NaiveDatetime, field_validator


VZOR_MENA = r"^[a-zA-Z0-9_.-]{3,40}$"


class Vstup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Prihlasenie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meno: str = Field(pattern=VZOR_MENA)
    heslo: str = Field(min_length=8, max_length=72)

    @field_validator("heslo")
    @classmethod
    def dlzka_hesla(cls, hodnota):
        if len(hodnota.encode("utf-8")) > 72:
            raise ValueError("Heslo môže mať najviac 72 bajtov v kódovaní UTF-8.")
        return hodnota


class UcetCSV(Vstup):
    id: int = Field(gt=0, le=2_147_483_647)
    mesto: str = Field(min_length=1, max_length=100)
    typ_uctu: str = Field(min_length=1, max_length=50)


class Transakcia(Vstup):
    ucet_id: int = Field(gt=0, le=2_147_483_647)
    cas: NaiveDatetime
    suma: Decimal = Field(max_digits=14, decimal_places=2)
    kategoria: str = Field(min_length=1, max_length=100)
    obchodnik: str = Field(min_length=1, max_length=120)

    @field_validator("cas", mode="before")
    @classmethod
    def format_casu(cls, hodnota):
        if isinstance(hodnota, datetime):
            return hodnota

        if isinstance(hodnota, str):
            try:
                return datetime.fromisoformat(hodnota)
            except ValueError:
                pass

        raise ValueError("Zadajte dátum a čas vo formáte RRRR-MM-DD HH:MM:SS.")


class TransakciaCSV(Transakcia):
    id: int = Field(gt=0, le=2_147_483_647)


class Pristup(Vstup):
    meno: str = Field(pattern=VZOR_MENA)


def sprava_validacie(chyba):
    typ = chyba["type"]
    kontext = chyba.get("ctx") or {}

    if typ == "value_error" and "error" in kontext:
        return str(kontext["error"])
    if typ == "string_too_short":
        return f"Text musí mať aspoň {kontext['min_length']} znakov."
    if typ == "string_too_long":
        return f"Text môže mať najviac {kontext['max_length']} znakov."

    return {
        "missing": "Hodnota je povinná.",
        "int_parsing": "Zadajte celé číslo.",
        "decimal_parsing": "Zadajte platnú sumu.",
        "decimal_max_places": "Suma môže mať najviac dve desatinné miesta.",
        "decimal_max_digits": "Suma môže mať najviac 14 číslic.",
        "timezone_naive": "Zadajte čas bez časového pásma.",
        "string_pattern_mismatch": (
            "Meno musí mať 3 až 40 znakov. "
            "Použite písmená bez diakritiky, číslice alebo znaky _, . a -."
        ),
        "json_invalid": "Telo požiadavky musí byť platný JSON.",
    }.get(typ, "Hodnota nespĺňa požiadavky poľa.")