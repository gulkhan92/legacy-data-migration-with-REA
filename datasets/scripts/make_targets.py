#!/usr/bin/env python3
"""Write the canonical target schema files (targets/commerce_target.json and clinic_target.json).

Each field has: table, name, type, required, description (one plain sentence), synonyms, examples
(three values) and pii. The descriptions are embedded and stored in pgvector by the mapping engine,
so they are written as the plain language a person would use to explain the field.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import TARGETS, write_json  # noqa: E402

KEY = "Identifier of the {entity} record in the legacy system."


def f(table, name, type_, required, description, synonyms, examples, pii=False):  # type: ignore[no-untyped-def]
    assert len(examples) == 3, (table, name)
    assert description.endswith(".") and description.count(". ") == 0, (table, name)
    return {
        "table": table, "name": name, "type": type_, "required": required, "description": description,
        "synonyms": synonyms, "examples": examples, "pii": pii,
    }  # fmt: skip


COMMERCE = [
    # customers
    f("customers", "source_key", "text", True, "Identifier of the customer record in the legacy system.",
      ["customer id", "customer number", "client number", "account number"], ["1", "25", "59"]),
    f("customers", "first_name", "text", False, "Given name of the customer.",
      ["forename", "given name", "first name"], ["Helena", "Mark", "Aaron"], True),
    f("customers", "last_name", "text", True, "Family name of the customer.",
      ["surname", "family name", "last name"], ["Holy", "Philips", "Mitchell"], True),
    f("customers", "company", "text", False, "Name of the company the customer works for.",
      ["organization", "employer", "business name"], ["Apple Inc.", "Microsoft Corporation", "Riotur"]),
    f("customers", "email", "text", True, "Email address used to contact the customer.",
      ["e-mail", "email address", "mail"], ["helena@example.test", "mphilips@example.test", "aaron@example.test"], True),
    f("customers", "phone", "text", False, "Telephone number of the customer.",
      ["telephone", "phone number", "contact number", "mobile"], ["+1 (650) 253-0000", "+55 (11) 3033-5446", "+49 030 26550280"], True),
    f("customers", "address_line1", "text", False, "Street address of the customer.",
      ["street", "address", "street address", "address line 1"], ["696 Osborne Street", "1 Microsoft Way", "Rotenturmstraße 4"], True),
    f("customers", "city", "text", False, "City where the customer lives.",
      ["town", "city name", "municipality"], ["Rio de Janeiro", "Boston", "Berlin"]),
    f("customers", "state", "text", False, "State or province where the customer lives.",
      ["province", "region", "state code"], ["CA", "MA", "SP"]),
    f("customers", "postal_code", "text", False, "Postal or ZIP code of the customer address.",
      ["zip", "zip code", "postcode", "postal"], ["94043", "70174", "12227-000"], True),
    f("customers", "country", "text", False, "Country where the customer lives.",
      ["nation", "country name"], ["USA", "Germany", "Brazil"]),
    # products
    f("products", "source_key", "text", True, "Identifier of the product record in the legacy system.",
      ["product id", "product number", "item number", "sku"], ["1", "1500", "3503"]),
    f("products", "name", "text", True, "Name of the product as shown to customers.",
      ["title", "product name", "item name", "description"], ["Balls to the Wall", "Fast As a Shark", "Princess of the Dawn"]),
    f("products", "category", "text", False, "Category or genre that the product belongs to.",
      ["genre", "type", "product group", "classification"], ["Rock", "Jazz", "Metal"]),
    f("products", "unit_price", "decimal", False, "Standard selling price of one unit of the product.",
      ["price", "list price", "unit cost", "amount"], ["0.99", "1.99", "0.99"]),
    # orders
    f("orders", "source_key", "text", True, "Identifier of the order record in the legacy system.",
      ["order id", "order number", "invoice number", "invoice id"], ["1", "205", "412"]),
    f("orders", "customer_id", "reference", True, "The customer who placed the order, referring to the customer source key.",
      ["customer", "customer number", "client", "buyer"], ["2", "14", "59"]),
    f("orders", "order_date", "date", True, "Date on which the order was placed.",
      ["invoice date", "purchase date", "date ordered", "order dt"], ["2009-01-01", "2011-06-15", "2013-12-22"]),
    f("orders", "status", "text", False, "Processing state of the order such as completed, pending or cancelled.",
      ["order status", "state", "status code"], ["completed", "pending", "cancelled"]),
    f("orders", "total_amount", "decimal", False, "Total amount charged for the whole order.",
      ["invoice total", "order total", "grand total", "total"], ["1.98", "13.86", "8.91"]),
    f("orders", "billing_city", "text", False, "City of the billing address on the order.",
      ["bill city", "invoice city", "billing town"], ["Stuttgart", "Oslo", "Paris"]),
    f("orders", "billing_country", "text", False, "Country of the billing address on the order.",
      ["bill country", "invoice country", "billing nation"], ["Germany", "Norway", "France"]),
    # order_items
    f("order_items", "source_key", "text", True, "Identifier of the order line record in the legacy system.",
      ["line id", "line number", "invoice line id", "detail number"], ["1", "1200", "2240"]),
    f("order_items", "order_id", "reference", True, "The order that the line belongs to, referring to the order source key.",
      ["invoice", "invoice number", "order number", "order"], ["1", "205", "412"]),
    f("order_items", "product_id", "reference", True, "The product sold on the line, referring to the product source key.",
      ["item", "track", "product number", "article"], ["2", "1500", "3503"]),
    f("order_items", "quantity", "integer", True, "Number of units of the product on the line.",
      ["qty", "units", "count", "number of items"], ["1", "1", "2"]),
    f("order_items", "unit_price", "decimal", False, "Price charged for one unit of the product on the line.",
      ["price", "line price", "unit cost", "rate"], ["0.99", "1.99", "0.99"]),
]

CLINIC = [
    # patients
    f("patients", "source_key", "text", True, "Identifier of the patient record in the legacy system.",
      ["patient id", "patient number", "mrn", "chart number"], ["1", "150", "306"]),
    f("patients", "first_name", "text", False, "Given name of the patient.",
      ["forename", "given name", "first name"], ["Sharyn", "Jose", "Maria"], True),
    f("patients", "last_name", "text", True, "Family name of the patient.",
      ["surname", "family name", "last name"], ["Hagenes", "Ortiz", "Smith"], True),
    f("patients", "birth_date", "date", True, "Date of birth of the patient.",
      ["date of birth", "dob", "birthdate", "born"], ["1951-04-12", "1987-09-30", "2003-01-15"], True),
    f("patients", "death_date", "date", False, "Date on which the patient died, empty while the patient is alive.",
      ["date of death", "deceased date", "dod"], ["2019-03-02", "2021-11-18", "2024-06-30"], True),
    f("patients", "gender", "text", False, "Gender of the patient recorded as M or F.",
      ["sex", "gender code", "male or female"], ["M", "F", "F"]),
    f("patients", "ssn", "text", False, "Social security number of the patient.",
      ["social security", "national id", "tax id"], ["999-10-8066", "999-23-4411", "999-77-0102"], True),
    f("patients", "address", "text", False, "Street address where the patient lives.",
      ["street", "street address", "home address", "residence"], ["663 Blick Plaza", "12 Maple Avenue", "88 Harbor Road"], True),
    f("patients", "city", "text", False, "City where the patient lives.",
      ["town", "city name", "municipality"], ["Milford", "Boston", "Worcester"]),
    f("patients", "state", "text", False, "State where the patient lives.",
      ["province", "region", "state name"], ["Massachusetts", "Massachusetts", "Massachusetts"]),
    f("patients", "postal_code", "text", False, "Postal or ZIP code of the patient address.",
      ["zip", "zip code", "postcode"], ["01757", "02134", "01604"], True),
    f("patients", "is_deceased", "boolean", False, "True when the patient has died.",
      ["deceased", "dead", "expired", "mortality flag"], ["true", "false", "false"]),
    # encounters
    f("encounters", "source_key", "text", True, "Identifier of the encounter record in the legacy system.",
      ["encounter id", "visit number", "visit id", "episode number"], ["1", "3000", "7242"]),
    f("encounters", "patient_id", "reference", True, "The patient who was seen, referring to the patient source key.",
      ["patient", "patient number", "subject"], ["1", "150", "306"]),
    f("encounters", "start_time", "timestamp", True, "Date and time at which the encounter began.",
      ["visit start", "admission time", "encounter date", "started"], ["2023-06-12T09:28:00Z", "2024-01-05T14:10:00Z", "2025-02-20T08:00:00Z"]),
    f("encounters", "end_time", "timestamp", False, "Date and time at which the encounter ended.",
      ["visit end", "discharge time", "stopped", "finished"], ["2023-06-12T09:43:00Z", "2024-01-05T14:40:00Z", "2025-02-20T08:15:00Z"]),
    f("encounters", "encounter_class", "text", False, "Kind of encounter such as ambulatory, emergency, inpatient or wellness.",
      ["visit type", "class", "encounter type", "setting"], ["ambulatory", "emergency", "wellness"]),
    f("encounters", "description", "text", False, "Short description of the reason or kind of the encounter.",
      ["visit description", "reason", "summary", "details"], ["Encounter for problem", "Well child visit", "Emergency room admission"]),
    f("encounters", "total_cost_cents", "integer", False, "Total cost of the encounter in cents.",
      ["cost", "claim cost", "charge", "billed amount"], ["7500", "14000", "251540"]),
    # conditions
    f("conditions", "source_key", "text", True, "Identifier of the condition record in the legacy system.",
      ["condition id", "diagnosis number", "problem number"], ["1", "2500", "5094"]),
    f("conditions", "patient_id", "reference", True, "The patient who has the condition, referring to the patient source key.",
      ["patient", "patient number", "subject"], ["1", "150", "306"]),
    f("conditions", "encounter_id", "reference", False, "The encounter in which the condition was recorded, referring to the encounter source key.",
      ["visit", "encounter", "visit number"], ["12", "3000", "7242"]),
    f("conditions", "code", "text", True, "Clinical code that identifies the condition.",
      ["diagnosis code", "snomed", "icd", "problem code"], ["314529007", "44054006", "15777000"]),
    f("conditions", "description", "text", False, "Plain language name of the condition.",
      ["diagnosis", "problem", "condition name", "disease"], ["Medication review due", "Diabetes mellitus type 2", "Prediabetes"]),
    f("conditions", "start_date", "date", False, "Date on which the condition began.",
      ["onset", "onset date", "diagnosed on", "from"], ["2023-06-03", "2020-02-11", "2018-09-27"]),
    f("conditions", "stop_date", "date", False, "Date on which the condition was resolved, empty while it continues.",
      ["resolved", "resolution date", "ended", "to"], ["2025-06-14", "2021-03-01", "2022-12-05"]),
    # medications
    f("medications", "source_key", "text", True, "Identifier of the medication record in the legacy system.",
      ["medication id", "prescription number", "rx number"], ["1", "2600", "5200"]),
    f("medications", "patient_id", "reference", True, "The patient who takes the medication, referring to the patient source key.",
      ["patient", "patient number", "subject"], ["1", "150", "306"]),
    f("medications", "encounter_id", "reference", False, "The encounter in which the medication was prescribed, referring to the encounter source key.",
      ["visit", "encounter", "visit number"], ["12", "3000", "7242"]),
    f("medications", "code", "text", True, "Drug code that identifies the medication.",
      ["rx code", "drug code", "rxnorm", "ndc"], ["313820", "197361", "834060"]),
    f("medications", "description", "text", False, "Plain language name of the medication including strength and form.",
      ["drug", "drug name", "prescription", "medicine"], ["Acetaminophen 160 MG Chewable Tablet", "Amlodipine 5 MG Oral Tablet", "Metformin hydrochloride 500 MG"]),
    f("medications", "start_date", "date", False, "Date on which the medication was started.",
      ["prescribed on", "start", "from", "begin date"], ["2024-01-19", "2022-05-30", "2019-11-02"]),
    f("medications", "stop_date", "date", False, "Date on which the medication was stopped, empty while it continues.",
      ["end date", "stopped on", "to", "discontinued"], ["2024-02-06", "2023-05-30", "2020-11-02"]),
    f("medications", "total_cost_cents", "integer", False, "Total cost of the medication in cents.",
      ["cost", "price", "charge", "amount billed"], ["5273", "1200", "18490"]),
    # observations
    f("observations", "source_key", "text", True, "Identifier of the observation record in the legacy system.",
      ["observation id", "result number", "measurement id"], ["1", "3000", "6120"]),
    f("observations", "patient_id", "reference", True, "The patient who was measured, referring to the patient source key.",
      ["patient", "patient number", "subject"], ["1", "150", "306"]),
    f("observations", "encounter_id", "reference", False, "The encounter during which the observation was made, referring to the encounter source key.",
      ["visit", "encounter", "visit number"], ["12", "3000", "7242"]),
    f("observations", "code", "text", True, "Clinical code that identifies what was measured.",
      ["loinc", "test code", "measurement code", "observation code"], ["8302-2", "29463-7", "8480-6"]),
    f("observations", "description", "text", False, "Plain language name of what was measured.",
      ["test name", "measurement", "observation", "parameter"], ["Body Height", "Body Weight", "Systolic Blood Pressure"]),
    f("observations", "value", "text", False, "Result of the measurement as recorded.",
      ["result", "reading", "measured value", "outcome"], ["122.3", "70.4", "118"]),
    f("observations", "units", "text", False, "Unit of measure of the result.",
      ["unit", "uom", "measure unit"], ["cm", "kg", "mm[Hg]"]),
    f("observations", "observed_at", "timestamp", False, "Date and time at which the measurement was taken.",
      ["measured at", "result time", "taken on", "observation time"], ["2024-06-08T05:05:00Z", "2023-11-14T10:20:00Z", "2025-01-30T16:45:00Z"]),
]


def main() -> None:
    write_json(TARGETS / "commerce_target.json", {
        "schema": "commerce", "version": "1",
        "description": "Canonical retail schema: customers, products, orders and order items.",
        "fields": COMMERCE,
    })  # fmt: skip
    write_json(TARGETS / "clinic_target.json", {
        "schema": "clinic", "version": "1",
        "description": "Canonical clinical schema: patients, encounters, conditions, medications and observations.",
        "fields": CLINIC,
    })  # fmt: skip
    print(f"targets written: commerce={len(COMMERCE)} fields, clinic={len(CLINIC)} fields")


if __name__ == "__main__":
    main()
