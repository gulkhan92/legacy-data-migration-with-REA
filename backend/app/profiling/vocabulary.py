"""Column name vocabulary: split legacy names into words and expand common abbreviations.

Used by the semantic typing and PII rules of the profiler. The mapping engine (next phase) extends the same
dictionary. Expansion is deliberately conservative: a short token is only expanded when it has one dominant meaning in
legacy business systems.
"""

import re

ABBREVIATIONS: dict[str, str] = {
    "cust": "customer", "customer": "customer", "client": "customer", "nm": "name", "nam": "name", "name": "name",
    "addr": "address", "adr": "address", "address": "address", "street": "street", "str": "street",
    "dt": "date", "date": "date", "tm": "time", "ts": "timestamp", "amt": "amount", "amount": "amount",
    "qty": "quantity", "quantity": "quantity", "ph": "phone", "tel": "phone", "phone": "phone", "fax": "fax",
    "mobile": "phone", "no": "number", "num": "number", "nbr": "number", "number": "number", "cd": "code",
    "code": "code", "ctry": "country", "cntry": "country", "country": "country", "st": "state", "state": "state",
    "zip": "zip", "zipcode": "zip", "postal": "postal", "postcode": "postal", "inv": "invoice", "invoice": "invoice",
    "prod": "product", "product": "product", "prc": "price", "price": "price", "tot": "total", "total": "total",
    "desc": "description", "dsc": "description", "descr": "description", "description": "description",
    "stat": "status", "sts": "status", "status": "status", "cat": "category", "category": "category",
    "dtl": "detail", "hdr": "header", "mst": "master", "bill": "billing", "billing": "billing", "f": "first",
    "l": "last", "first": "first", "last": "last", "middle": "middle", "sur": "surname", "surname": "surname",
    "given": "given", "fore": "forename", "forename": "forename", "maiden": "maiden", "co": "company",
    "company": "company", "comp": "company", "len": "length", "ms": "milliseconds", "sz": "size", "pat": "patient",
    "patient": "patient", "enc": "encounter", "encounter": "encounter", "dx": "diagnosis", "rx": "prescription",
    "obs": "observation", "val": "value", "value": "value", "min": "minutes", "cond": "condition", "med": "medication",
    "exp": "expenses", "expenses": "expenses", "cents": "cents", "city": "city", "email": "email", "mail": "email",
    "e": "email", "id": "id", "key": "key", "ssn": "ssn", "dob": "birth", "birth": "birth", "death": "death",
    "deceased": "death", "income": "income", "amt_": "amount", "onset": "onset", "resolv": "resolution",
    "start": "start", "stop": "stop", "end": "end", "ethnicity": "ethnicity", "race": "race", "gender": "gender",
    "sex": "gender", "marital": "marital", "unit": "unit", "units": "unit", "ref": "reference", "seq": "sequence",
    "flag": "flag", "flags": "flag", "class": "class", "type": "type", "cost": "cost", "fee": "fee",
    "balance": "balance", "bal": "balance", "rate": "rate", "sku": "sku", "composer": "composer", "encntr": "encounter", "meds": "medication", "txt": "text", "unt": "unit",
    "log": "log", "sys": "system",
}  # fmt: skip

COMPOUNDS: dict[str, list[str]] = {
    "birthdate": ["birth", "date"], "deathdate": ["death", "date"], "firstname": ["first", "name"],
    "lastname": ["last", "name"], "surname": ["surname"], "zipcode": ["zip", "code"], "postalcode": ["postal", "code"],
    "phonenumber": ["phone", "number"], "healthcare": ["healthcare"], "emailaddress": ["email", "address"],
    "fname": ["first", "name"], "lname": ["last", "name"], "firstnm": ["first", "name"], "lastnm": ["last", "name"],
    "streetaddress": ["street", "address"],
}  # fmt: skip

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize(name: str) -> list[str]:
    """Lower case words of a column name: splits on separators, camel case and trailing digits."""
    parts: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name):
        if not chunk:
            continue
        for piece in _CAMEL.split(chunk):
            piece = re.sub(r"\d+$", "", piece) or piece  # addr1 -> addr
            if piece:
                parts.append(piece.lower())
    return parts


def expand(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for token in tokens:
        if token in COMPOUNDS:
            out.extend(COMPOUNDS[token])
        else:
            out.append(ABBREVIATIONS.get(token, token))
    return out


def expanded_words(name: str) -> list[str]:
    return expand(tokenize(name))


def expanded_name(name: str) -> str:
    return " ".join(expanded_words(name))
