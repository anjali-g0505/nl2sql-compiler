"""
generate_data.py — deterministic synthetic card-transaction data for card_analytics.

Outputs (into ./output/ next to this script):
  * one CSV per table (7 files)
  * 01_schema.sql  — CREATE DATABASE + CREATE TABLE with PKs/FKs
  * 02_data.sql    — INSERTs, masters first, then card_txns

Pure stdlib. Customer PII is built from fixed in-script word lists rather than Faker,
because Faker's output for a given seed changes between Faker versions; stdlib `random`
with a fixed seed gives byte-identical output on every run and every machine.
"""
import random

random.seed(42)

import csv
import os
from collections import Counter

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

N_CUSTOMERS = 100
N_TXNS = 3000
YEAR = 2025

# ---------------------------------------------------------------------------
# Fixed master tables (exactly as specified)
# ---------------------------------------------------------------------------
ISSUER_ROWS = [
    ("ISS001", "State Bank of India (SBI)"),
    ("ISS002", "HDFC Bank"),
    ("ISS003", "HSBC Bank"),
]

BIN_ROWS = [
    ("608011", "Credit", "ISS001", "Classic"),
    ("608012", "Credit", "ISS001", "Business"),
    ("608013", "Credit", "ISS001", "Platinum"),
    ("608021", "Credit", "ISS002", "Classic"),
    ("608022", "Credit", "ISS002", "Business"),
    ("608023", "Credit", "ISS002", "Platinum"),
    ("608031", "Credit", "ISS003", "Classic"),
    ("608032", "Credit", "ISS003", "Business"),
    ("608033", "Credit", "ISS003", "Platinum"),
    ("608111", "Debit", "ISS001", "Classic"),
    ("608112", "Debit", "ISS001", "Business"),
    ("608113", "Debit", "ISS001", "Platinum"),
    ("608121", "Debit", "ISS002", "Classic"),
    ("608122", "Debit", "ISS002", "Business"),
    ("608123", "Debit", "ISS002", "Platinum"),
    ("608131", "Debit", "ISS003", "Classic"),
    ("608132", "Debit", "ISS003", "Business"),
    ("608133", "Debit", "ISS003", "Platinum"),
    ("608211", "Prepaid", "ISS001", "Classic"),
    ("608212", "Prepaid", "ISS001", "Business"),
    ("608213", "Prepaid", "ISS001", "Platinum"),
    ("608221", "Prepaid", "ISS002", "Classic"),
    ("608222", "Prepaid", "ISS002", "Business"),
    ("608223", "Prepaid", "ISS002", "Platinum"),
    ("608231", "Prepaid", "ISS003", "Classic"),
    ("608232", "Prepaid", "ISS003", "Business"),
    ("608233", "Prepaid", "ISS003", "Platinum"),
]

RESPONSE_ROWS = [
    ("00", "Approved", "Success"),
    ("05", "Do not honor", "Business Decline"),
    ("14", "Invalid card number", "Business Decline"),
    ("41", "Lost card", "Business Decline"),
    ("43", "Stolen card", "Business Decline"),
    ("51", "Insufficient funds", "Business Decline"),
    ("54", "Expired card", "Business Decline"),
    ("57", "Transaction not permitted to cardholder", "Business Decline"),
    ("61", "Exceeds withdrawal amount limit", "Business Decline"),
    ("68", "Response received too late (timeout)", "Technical Decline"),
    ("91", "Issuer or switch inoperative", "Technical Decline"),
    ("92", "Financial institution not found (routing error)", "Technical Decline"),
    ("96", "System malfunction", "Technical Decline"),
]

# ---------------------------------------------------------------------------
# Generated lookup tables
# ---------------------------------------------------------------------------
ACQUIRER_ROWS = [
    ("ACQ001", "Axis Bank"),
    ("ACQ002", "ICICI Bank"),
    ("ACQ003", "RBL Bank"),
    ("ACQ004", "Worldline"),
    ("ACQ005", "Razorpay"),
]

MCC_DESCRIPTIONS = {
    "5411": "Grocery Stores and Supermarkets",
    "5812": "Eating Places and Restaurants",
    "5541": "Service Stations",
    "5311": "Department Stores",
    "4111": "Local and Suburban Commuter Transport",
    "5912": "Drug Stores and Pharmacies",
    "5732": "Electronics Stores",
    "7011": "Lodging - Hotels",
    "4899": "Cable, Satellite and Streaming Services",
    "5999": "Miscellaneous and Specialty Retail",
}

# Two merchants per MCC, names matching the category.
MERCHANT_NAMES = [
    ("5411", "FreshBasket Supermart"),
    ("5812", "Spice Route Kitchen"),
    ("5541", "Highway Fuel Point"),
    ("5311", "Metro Lifestyle Department Store"),
    ("4111", "CityLink Metro Rail"),
    ("5912", "WellCare Pharmacy"),
    ("5732", "Digital Planet Electronics"),
    ("7011", "Seaview Grand Hotel"),
    ("4899", "StreamBox Digital TV"),
    ("5999", "Artisan Gift Emporium"),
    ("5411", "Daily Needs Grocery Mart"),
    ("5812", "Tandoor Nights Restaurant"),
    ("5541", "GreenLine Service Station"),
    ("5311", "Central Bazaar Stores"),
    ("4111", "Urban Commute Bus Services"),
    ("5912", "MediPlus Chemists"),
    ("5732", "Gadget Hub Electronics"),
    ("7011", "Heritage Residency Hotel"),
    ("4899", "SkyWave Cable Network"),
    ("5999", "Curio Corner Specialty Retail"),
]

# Word lists for clearly synthetic Indian customer PII.
FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Kabir", "Aryan", "Dhruv", "Karan", "Nikhil", "Rahul",
    "Aanya", "Diya", "Ananya", "Saanvi", "Ira", "Myra", "Priya", "Kavya",
    "Meera", "Neha", "Pooja", "Riya", "Sneha", "Tanvi", "Aditi", "Lakshmi",
]
LAST_NAMES = [
    "Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta",
    "Gupta", "Singh", "Kumar", "Das", "Banerjee", "Chatterjee", "Joshi", "Kulkarni",
    "Menon", "Pillai", "Rao", "Bhat", "Desai", "Chopra", "Malhotra", "Agarwal",
]
STREETS = [
    "MG Road", "Station Road", "Park Street", "Link Road", "Nehru Marg",
    "Gandhi Nagar Main Road", "Church Street", "Temple Road", "Lake View Road",
    "Ring Road", "Market Road", "College Road",
]
# (city, state, PIN prefix)
CUSTOMER_CITIES = [
    ("Mumbai", "Maharashtra", "400"),
    ("Delhi", "Delhi", "110"),
    ("Bengaluru", "Karnataka", "560"),
    ("Chennai", "Tamil Nadu", "600"),
    ("Pune", "Maharashtra", "411"),
    ("Hyderabad", "Telangana", "500"),
    ("Kolkata", "West Bengal", "700"),
    ("Ahmedabad", "Gujarat", "380"),
    ("Jaipur", "Rajasthan", "302"),
    ("Kochi", "Kerala", "682"),
]

# ---------------------------------------------------------------------------
# Fact-table distributions
# ---------------------------------------------------------------------------
TD_BD_SHARE = {"Success": 0.88, "Business Decline": 0.08, "Technical Decline": 0.04}

# Relative frequency of each code within its TD_BD bucket.
CODE_WEIGHTS = {
    "00": 1,
    "05": 20, "14": 6, "41": 3, "43": 2, "51": 35, "54": 12, "57": 12, "61": 10,
    "68": 30, "91": 30, "92": 10, "96": 30,
}

COUNTRY_WEIGHTS = [("IN", 85), ("US", 5), ("GB", 4), ("AE", 3), ("SG", 3)]
CITIES_BY_COUNTRY = {
    "IN": ["Mumbai", "Delhi", "Bangalore", "Chennai", "Pune", "Hyderabad", "Kolkata"],
    "US": ["New York", "San Francisco", "Chicago", "Los Angeles"],
    "GB": ["London", "Manchester", "Birmingham"],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "SG": ["Singapore"],
}

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # 2025 is not a leap year
MONTHLY_GROWTH = 0.04  # mild month-over-month volume increase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def allocate(total, weights):
    """Split `total` into integer counts proportional to `weights` (largest remainder)."""
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    counts = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - counts[i]), i))
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts


def gen_amount():
    """Right-skewed rupee amount: mostly 100-5000, a tail to 50000, rare larger outliers."""
    r = random.random()
    if r < 0.01:    # ~1% large outliers
        amt = random.uniform(50000, 250000)
    elif r < 0.10:  # ~9% tail, skewed toward the low end of 5000-50000
        amt = min(max(random.lognormvariate(9.3, 0.6), 5000), 50000)
    else:           # ~90% everyday spend
        amt = min(max(random.lognormvariate(6.9, 0.8), 100), 5000)
    return f"{amt:.2f}"


def sql_literal(value):
    if value is None:
        return "NULL"
    s = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


# ---------------------------------------------------------------------------
# Build masters as in-memory dicts
# ---------------------------------------------------------------------------
def build_masters():
    issuer_master = {iid: {"id": iid, "iss_name": name} for iid, name in ISSUER_ROWS}

    bin_master = {
        b: {"BIN": b, "card_type": ct, "issuer_id": iss, "card_variant": var}
        for b, ct, iss, var in BIN_ROWS
    }

    response_master = {
        code: {"response_code": code, "response_description": desc, "TD_BD": tdbd}
        for code, desc, tdbd in RESPONSE_ROWS
    }

    acquirer_master = {aid: {"id": aid, "acq_name": name} for aid, name in ACQUIRER_ROWS}

    acquirer_ids = list(acquirer_master)
    merchant_master = {}
    for i, (mcc, name) in enumerate(MERCHANT_NAMES, start=1):
        mid = f"MER{i:04d}"
        merchant_master[mid] = {
            "merchant_id": mid,
            "acquirer_id": acquirer_ids[(i - 1) % len(acquirer_ids)],  # every acquirer gets 4
            "name": name,
            "mcc_code": mcc,
            "mcc_description": MCC_DESCRIPTIONS[mcc],
        }

    customer_master = {}
    used_names = set()
    for i in range(1, N_CUSTOMERS + 1):
        cid = f"CUST{i:04d}"
        while True:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        phone = f"+91 {random.choice('6789')}{random.randint(0, 999999999):09d}"
        city, state, pin_prefix = random.choice(CUSTOMER_CITIES)
        address = (
            f"{random.randint(1, 250)}, {random.choice(STREETS)}, "
            f"{city}, {state} {pin_prefix}{random.randint(1, 99):03d}"
        )
        customer_master[cid] = {"id": cid, "name": name, "phone_number": phone, "address": address}

    return {
        "issuer_master": issuer_master,
        "acquirer_master": acquirer_master,
        "BIN_master": bin_master,
        "response_master": response_master,
        "merchant_master": merchant_master,
        "customer_master": customer_master,
    }


# ---------------------------------------------------------------------------
# Build fact table
# ---------------------------------------------------------------------------
def build_card_txns(m):
    bin_master = m["BIN_master"]
    merchant_master = m["merchant_master"]
    response_master = m["response_master"]
    bins = list(bin_master)
    merchants = list(merchant_master)
    customers = list(m["customer_master"])

    # Dates: exact per-month quotas with a mild upward trend, sorted chronologically.
    month_counts = allocate(N_TXNS, [(1 + MONTHLY_GROWTH) ** k for k in range(12)])
    dates = []
    for month, count in enumerate(month_counts, start=1):
        for _ in range(count):
            day = random.randint(1, MONTH_DAYS[month - 1])
            dates.append(f"{YEAR}-{month:02d}-{day:02d}")
    dates.sort()

    # Response codes: exact TD_BD quotas, then a code within each bucket, then shuffle.
    buckets = list(TD_BD_SHARE)
    bucket_counts = allocate(N_TXNS, [TD_BD_SHARE[b] for b in buckets])
    codes = []
    for bucket, count in zip(buckets, bucket_counts):
        bucket_codes = [c for c, r in response_master.items() if r["TD_BD"] == bucket]
        codes.extend(random.choices(bucket_codes, weights=[CODE_WEIGHTS[c] for c in bucket_codes], k=count))
    random.shuffle(codes)

    countries = [c for c, _ in COUNTRY_WEIGHTS]
    country_w = [w for _, w in COUNTRY_WEIGHTS]

    txns = []
    for i in range(N_TXNS):
        bin_ = random.choice(bins)
        issuer_id = bin_master[bin_]["issuer_id"]                # derived
        merchant_id = random.choice(merchants)
        acquirer_id = merchant_master[merchant_id]["acquirer_id"]  # derived
        country = random.choices(countries, weights=country_w, k=1)[0]
        txns.append({
            "txn_id": f"TXN{i + 1:06d}",
            "amt": gen_amount(),
            "date": dates[i],
            "issuer_id": issuer_id,
            "acquirer_id": acquirer_id,
            "merchant_id": merchant_id,
            "customer_id": random.choice(customers),
            "BIN": bin_,
            "country": country,
            "location": random.choice(CITIES_BY_COUNTRY[country]),
            "response_code": codes[i],
        })
    return txns


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# (table, [(column, type)], primary key, [(fk column, ref table, ref column)])
TABLES = [
    ("issuer_master",
     [("id", "VARCHAR(10)"), ("iss_name", "VARCHAR(100)")],
     "id", []),
    ("acquirer_master",
     [("id", "VARCHAR(10)"), ("acq_name", "VARCHAR(100)")],
     "id", []),
    ("BIN_master",
     [("BIN", "VARCHAR(6)"), ("card_type", "VARCHAR(20)"),
      ("issuer_id", "VARCHAR(10)"), ("card_variant", "VARCHAR(20)")],
     "BIN", [("issuer_id", "issuer_master", "id")]),
    ("response_master",
     [("response_code", "VARCHAR(2)"), ("response_description", "VARCHAR(100)"),
      ("TD_BD", "VARCHAR(20)")],
     "response_code", []),
    ("merchant_master",
     [("merchant_id", "VARCHAR(10)"), ("acquirer_id", "VARCHAR(10)"), ("name", "VARCHAR(100)"),
      ("mcc_code", "VARCHAR(4)"), ("mcc_description", "VARCHAR(100)")],
     "merchant_id", [("acquirer_id", "acquirer_master", "id")]),
    ("customer_master",
     [("id", "VARCHAR(10)"), ("name", "VARCHAR(100)"), ("phone_number", "VARCHAR(16)"),
      ("address", "VARCHAR(255)")],
     "id", []),
    ("card_txns",
     [("txn_id", "VARCHAR(12)"), ("amt", "DECIMAL(12,2)"), ("date", "VARCHAR(10)"),
      ("issuer_id", "VARCHAR(10)"), ("acquirer_id", "VARCHAR(10)"),
      ("merchant_id", "VARCHAR(10)"), ("customer_id", "VARCHAR(10)"),
      ("BIN", "VARCHAR(6)"), ("country", "VARCHAR(2)"), ("location", "VARCHAR(50)"),
      ("response_code", "VARCHAR(2)")],
     "txn_id",
     [("issuer_id", "issuer_master", "id"),
      ("acquirer_id", "acquirer_master", "id"),
      ("merchant_id", "merchant_master", "merchant_id"),
      ("customer_id", "customer_master", "id"),
      ("BIN", "BIN_master", "BIN"),
      ("response_code", "response_master", "response_code")]),
]


def check_fk_types():
    types = {(t, c): ty for t, cols, _, _ in TABLES for c, ty in cols}
    for t, _, _, fks in TABLES:
        for col, ref_t, ref_c in fks:
            assert types[(t, col)] == types[(ref_t, ref_c)], f"FK type mismatch {t}.{col} -> {ref_t}.{ref_c}"


def write_schema(path):
    lines = [
        "-- Generated by generate_data.py. Do not edit by hand.",
        "CREATE DATABASE IF NOT EXISTS card_analytics;",
        "USE card_analytics;",
        "",
        "SET FOREIGN_KEY_CHECKS = 0;",
    ]
    for t, _, _, _ in reversed(TABLES):
        lines.append(f"DROP TABLE IF EXISTS `{t}`;")
    lines += ["SET FOREIGN_KEY_CHECKS = 1;", ""]

    for t, cols, pk, fks in TABLES:
        body = [f"  `{c}` {ty} NOT NULL" for c, ty in cols]
        body.append(f"  PRIMARY KEY (`{pk}`)")
        for col, ref_t, ref_c in fks:
            body.append(
                f"  CONSTRAINT `fk_{t}_{col}` FOREIGN KEY (`{col}`) REFERENCES `{ref_t}` (`{ref_c}`)"
            )
        lines.append(f"CREATE TABLE `{t}` (")
        lines.append(",\n".join(body))
        lines.append(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
        lines.append("")

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def write_data(path, table_rows, batch_size=500):
    lines = [
        "-- Generated by generate_data.py. Do not edit by hand.",
        "USE card_analytics;",
        "SET NAMES utf8mb4;",
        "",
    ]
    for t, cols, _, _ in TABLES:  # TABLES is already in dependency order
        col_names = [c for c, _ in cols]
        col_sql = ", ".join(f"`{c}`" for c in col_names)
        rows = table_rows[t]
        lines.append(f"-- {t}: {len(rows)} rows")
        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            values = ",\n".join(
                "  (" + ", ".join(
                    r[c] if c == "amt" else sql_literal(r[c]) for c in col_names
                ) + ")"
                for r in chunk
            )
            lines.append(f"INSERT INTO `{t}` ({col_sql}) VALUES\n{values};")
        lines.append("")

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[c for c, _ in cols], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(m, txns, table_rows):
    print("=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print("Row counts:")
    for t, _, _, _ in TABLES:
        print(f"  {t:<18} {len(table_rows[t]):>6}")

    pk_index = {t: {r[pk] for r in table_rows[t]} for t, _, pk, _ in TABLES}
    dangling = 0
    for t, _, pk, fks in TABLES:
        assert len(pk_index[t]) == len(table_rows[t]), f"duplicate PK in {t}"
        for col, ref_t, ref_c in fks:
            ref_values = {r[ref_c] for r in table_rows[ref_t]}
            dangling += sum(1 for r in table_rows[t] if r[col] not in ref_values)

    # Derived values must also agree with their source master rows.
    inconsistent = sum(
        1 for r in txns
        if r["issuer_id"] != m["BIN_master"][r["BIN"]]["issuer_id"]
        or r["acquirer_id"] != m["merchant_master"][r["merchant_id"]]["acquirer_id"]
    )
    assert dangling == 0, f"{dangling} dangling foreign keys"
    assert inconsistent == 0, f"{inconsistent} txns with issuer/acquirer inconsistent with masters"
    print(f"\nDangling foreign keys: {dangling}  (assert passed)")
    print(f"Derived issuer/acquirer mismatches: {inconsistent}  (assert passed)")

    months = sorted(Counter(r["date"][:7] for r in txns).items())
    assert len(months) == 12, "not all 12 months present"
    print("\nTransactions per month:")
    for ym, n in months:
        print(f"  {ym}  {n:>4}")

    split = Counter(m["response_master"][r["response_code"]]["TD_BD"] for r in txns)
    print("\nTD_BD split:")
    for bucket in TD_BD_SHARE:
        print(f"  {bucket:<18} {split[bucket]:>5}  {100 * split[bucket] / len(txns):6.2f}%")

    countries = Counter(r["country"] for r in txns)
    print("\nCountry split: " + ", ".join(
        f"{c} {100 * n / len(txns):.1f}%" for c, n in countries.most_common()))

    amts = sorted(float(r["amt"]) for r in txns)
    print(f"\namt: min {amts[0]:.2f}, median {amts[len(amts) // 2]:.2f}, "
          f"max {amts[-1]:.2f}, >50000: {sum(a > 50000 for a in amts)}")
    print("=" * 60)


def main():
    check_fk_types()
    m = build_masters()
    txns = build_card_txns(m)

    table_rows = {t: list(rows.values()) for t, rows in m.items()}
    table_rows["card_txns"] = txns

    os.makedirs(OUT_DIR, exist_ok=True)
    for t, cols, _, _ in TABLES:
        write_csv(os.path.join(OUT_DIR, f"{t}.csv"), cols, table_rows[t])
    write_schema(os.path.join(OUT_DIR, "01_schema.sql"))
    write_data(os.path.join(OUT_DIR, "02_data.sql"), table_rows)

    print(f"Wrote 7 CSVs, 01_schema.sql and 02_data.sql to {OUT_DIR}\n")
    validate(m, txns, table_rows)


if __name__ == "__main__":
    main()
