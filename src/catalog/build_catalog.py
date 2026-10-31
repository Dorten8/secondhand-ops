#!/usr/bin/env python3
"""
build_catalog.py
Pre-builds the exact, structured listing dictionary for all 46 items.
Strictly adheres to:
1. Title standard: WHAT - WHATTYPE - MAKE
2. Bilingual description: (English below) -> Danish -> '---' -> English -> Pickup terms (2300 København S, MobilePay)
3. Verified curated photos on disk.
"""

import json
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
ALL_LISTINGS_PATH = PROJECT_DIR / "ALL_LISTINGS.md"
ITEMS_DIR = PROJECT_DIR / "items"
OUTPUT_CATALOG_PATH = PROJECT_DIR / "catalog_prebuilt.json"

# Explicit title overrides to guarantee 100% adherence to WHAT - WHATTYPE - MAKE
TITLES = {
    # Wheelsets
    "WS01": 'Wheelset / Hjulsæt - 29", MTB Disc (9-Speed) - Busetto Grande',
    "WS02": 'Wheelset / Hjulsæt - 26", MTB Disc - Alexrims XD 2500',
    "WS03": 'Wheelset / Hjulsæt - 28", E-Bike 36V / Nexus 7 - Ryde Zac 421',
    "WS04": 'Wheelset / Hjulsæt - 28", City Commuter (7-Speed) - Shimano',
    # Platforms / Racks
    "P01": 'Rear Carrier / Bagagebærer - 28", Stål med Refleks - Universal',
    "P02": 'Battery Carrier / Batteribagagebærer - 28", E-Bike - Shimano Steps',
    "P03": 'Front Carrier / Frontlad - 28", Stål Blå - Universal',
    "P04": 'Rear Carrier / Bagagebærer - 28", Stål Sort - Universal',
    "P05": 'Front Carrier / Frontlad - 28", Aluminium med Lygte - Universal',
    "P06": 'Front Carrier / Frontlad - 28", Kraftig med Kurv - Universal',
    "P07": 'Rear Carrier / Bagagebærer - 28", Aluminium - Universal',
    # Cockpit
    "HB01": 'Cockpit / Styr & Bremser - 31.8mm, Hydraulisk - Shimano Altus 3x7',
    # Tires
    "T01": 'Tires / Cykeldæk - 28x1.75 (47-622), Brun - Schwalbe Citizen (Par)',
    "T02": 'Tires / Cykeldæk - 28x1.40 (37-622) - Schwalbe Delta Cruiser (Par)',
    "T03": 'Tires / Cykeldæk - 28" (37-622), Creme - Schwalbe Delta Cruiser (Par)',
    "T04": 'Cargo Tire / Ladcykeldæk - 26x2.15 (55-559) - Schwalbe Pick-Up',
    "T05": 'Tire / Cykeldæk - 28x1.75 (47-622), Pink/Sort - Deli Tire',
    "T06": 'MTB Tire / Mountainbikedæk - 29x2.10 (54-622) - Yosemite',
    "T07": 'Tire / Cykeldæk - 700x40C (42-622), Punkterfri - Innova',
    # Front Wheels
    "WF01": 'Front Wheel / Forhjul - 28", E-Bike 36V 250W Motor - Ryde Zac 421',
    "WF02": 'Front Wheel / Forhjul - 27.5", MTB Skivebremse - Schwalbe / Disc',
    "WF03": 'Front Wheel / Forhjul - 29", MTB Skivebremse - Busetto Grande',
    "WF04": 'Front Wheel / Forhjul - 26", MTB - Alexrims TD26',
    "WF05": 'Front Wheel / Forhjul - 28", Dynamonav - Shimano DH-C3000-3N-NT',
    "WF06": 'Front Wheel / Forhjul - 28", Deep-V Fælgbremse - Davos',
    "WF07": 'Front Wheel / Forhjul - 28", 19mm Fælgbremse - City',
    "WF08": 'Front Wheel / Forhjul - 28", Vintage 90mm Aksel - Classic',
    "WF09": 'Front Wheel / Forhjul - 28" (700c), Deep-V Møtrikaksel - City',
    "WF10": 'Front Wheel / Forhjul - 26", MTB med Dæk - Shining',
    "WF11": 'Front Wheel / Forhjul - 28", Vintage / City Fælgbremse - Classic',
    "WF12": 'Front Wheel / Forhjul - 28", Fast Aksel / City - Classic',
    "WF13": 'Front Wheel / Forhjul - 28", 100mm Nav (Reservedele/Rep) - City',
    "WF14": 'Front Wheel / Forhjul - 28", Fælgbremse QR - Centurion',
    "WF15": 'Front Wheel / Forhjul - 28", Rullebremse Rød Fælg - Batavus',
    # Rear Wheels - Cassette / Freewheel
    "WRC01": 'Rear Wheel / Baghjul - 28", 7-Speed Skruekrans - Shimano',
    "WRC02": 'Rear Wheel / Baghjul - 28" (700c), Skivebremse - Disc',
    "WRC03": 'Rear Wheel / Baghjul - 28" (700c), Fixie / Single Speed - Flip-Flop',
    "WRC04": 'Rear Wheel / Baghjul - 26", MTB Skivebremse - Alexrims XD 2500',
    "WRC05": 'Rear Wheel / Baghjul - 28" (700c), Kassette QR - Liv / Giant',
    "WRC06": 'Rear Wheel / Baghjul - 29", MTB Skivebremse 9-Speed - Busetto Grande',
    # Rear Wheels - Internal Hub Gear / Coaster
    "WRS01": 'Rear Wheel / Baghjul - 28", 7-Speed Fodbremse - Shimano Nexus 7',
    "WRS02": 'Rear Wheel / Baghjul - 28", 7-Speed Fodbremse Sølv - Shimano Nexus 7',
    "WRS03": 'Rear Wheel / Baghjul - 28", 7-Speed Fodbremse - Jalco / Nexus 7',
    "WRS04": 'Rear Wheel / Baghjul - 28", 7-Speed Fodbremse - Ryde Zac 421 / Nexus 7',
    "WRS05": 'Rear Wheel / Baghjul - 28", 7-Speed Rulle/Skivebremse - Mach 1 / Nexus 7',
    "WRS06": 'Rear Wheel / Baghjul - 24", 8-Speed Ladcykel - Shimano Nexus 8',
}

# Danish translation dictionaries for common terms
TRANSLATIONS = {
    "Compatibility": "Kompatibilitet",
    "Wheel Size": "Hjulstørrelse",
    "Rim": "Fælg",
    "Rims": "Fælge",
    "Brake Type": "Bremsetype",
    "Brake Compatibility": "Bremsekompatibilitet",
    "Axle Type": "Akseltype",
    "Axle / Spacing": "Aksel / Navbredde",
    "Drivetrain / Gearing": "Gear / Kassette",
    "Tire": "Dæk",
    "Tires": "Dæk",
    "Valve": "Ventil",
    "Front Wheel": "Forhjul",
    "Rear Wheel": "Baghjul",
    "Bundle Savings": "Samlerabat",
    "Material": "Materiale",
    "Weight Capacity": "Bæreevne",
    "Dimensions": "Dimensioner",
    "Mounting": "Montering",
    "Special Features": "Særlige funktioner",
    "Battery Compatibility": "Batterikompatibilitet",
    "Components": "Komponenter",
    "Handlebar": "Styre",
    "Brakes": "Bremser",
    "Shifters": "Gearskiftere",
    "Tread Condition": "Dækmønster",
    "Wheel Position": "Placering",
}

def translate_spec_line(line: str) -> str:
    if ":" in line:
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        dk_k = TRANSLATIONS.get(k, k)
        # translate common values
        dk_v = v
        dk_v = dk_v.replace("included", "medfølger")
        dk_v = dk_v.replace("Quick Release", "Quick Release (hurtigspænder)")
        dk_v = dk_v.replace("Coaster brake (backpedal brake)", "Fodbremse (tilbagetræd)")
        dk_v = dk_v.replace("Coaster brake", "Fodbremse")
        dk_v = dk_v.replace("Roller brake", "Rullebremse")
        dk_v = dk_v.replace("Rim brake", "Fælgbremse")
        dk_v = dk_v.replace("Disc brake", "Skivebremse")
        dk_v = dk_v.replace("Aluminum", "Aluminium")
        dk_v = dk_v.replace("Steel", "Stål")
        return f"• {dk_k}: {dk_v}"
    return f"• {line}"

def generate_danish_intro(item_id: str, title: str, eng_desc: str) -> str:
    # High-quality natural Danish summary
    # Wheelsets
    if item_id.startswith("WS"):
        return f"Komplet og matchende hjulsæt i god stand ({title.split(' - ')[-1]}). Lige fælge og velsmurte lejer klar til montering."
    # Platforms / Racks
    if item_id.startswith("P"):
        return f"Solidt og funktionelt cykelstativ / lad ({title.split(' - ')[-1]}). Perfekt til hverdagspendling, transport eller elcykel."
    # Tires
    if item_id.startswith("T"):
        return f"Kvalitets cykeldæk ({title.split(' - ')[-1]}) med masser af mønster tilbage og god punkteringsbeskyttelse."
    # Cockpit
    if item_id.startswith("HB"):
        return f"Komplet mountainbike / citybike cockpit med 31.8mm styr, velfungerende hydrauliske skivebremser og Shimano 3x7 gearskiftere."
    # Front Wheels
    if item_id.startswith("WF"):
        return f"Solidt forhjul til cykel ({title.split(' - ')[-1]}). Ruller lige og jævnt, klar til montering."
    # Rear Wheels
    if item_id.startswith("WRC") or item_id.startswith("WRS"):
        return f"Velfungerende baghjul til cykel ({title.split(' - ')[-1]}). Lige fælg, fine lejer og pålidelig funktion."
    return f"Velholdt cykeldel ({title.split(' - ')[-1]}). Klar til brug."

def build_unified_description(item_id: str, title: str, eng_desc: str, specs_lines: list) -> str:
    # Danish Section
    dk_intro = generate_danish_intro(item_id, title, eng_desc)
    dk_specs = "\n".join(translate_spec_line(s) for s in specs_lines)
    
    # English Section
    en_specs = "\n".join(f"• {s}" for s in specs_lines)
    
    desc = f"""(English below)

{dk_intro}

Specifikationer:
• Vare-ID: #{item_id}
{dk_specs}
• Stand: God brugt stand

Afhentning og betaling:
• Sendes ikke – kun personlig afhentning i 2300 København S.
• MobilePay modtages.

---

{eng_desc}

Specifications:
• Item ID: #{item_id}
{en_specs}
• Condition: Used - Good

Pickup & Payment:
• No shipping – personal pickup only in 2300 Copenhagen S.
• MobilePay accepted."""
    return desc.strip()

def get_fb_attributes(item_id: str, title: str, specs_lines: list):
    full_text = title + " " + " ".join(specs_lines)
    wheel_size = None
    if '26"' in full_text or '559' in full_text:
        wheel_size = "26 in"
    elif '29"' in full_text:
        wheel_size = "29 in"
    elif '27.5"' in full_text or '584' in full_text:
        wheel_size = "27.5 in"
    elif '24"' in full_text or '507' in full_text:
        wheel_size = "24 in"
    elif '28"' in full_text or '700c' in full_text or '622' in full_text:
        wheel_size = "28 in"
    
    mat = None
    if "Alumin" in full_text or "Alloy" in full_text:
        mat = "Aluminum"
    elif "Stål" in full_text or "Steel" in full_text:
        mat = "Steel"
        
    b_type = None
    if "MTB" in full_text or "Mountain" in full_text:
        b_type = "Mountain Bike"
    elif "E-Bike" in full_text or "Elcykel" in full_text:
        b_type = "Electric Bicycle"
    elif "Fixie" in full_text or "Single Speed" in full_text:
        b_type = "Single Speed"
    elif "Cargo" in full_text or "Ladcykel" in full_text:
        b_type = "Cargo Bike"
    elif "City" in full_text or "Commuter" in full_text:
        b_type = "City Bike"
        
    brand = None
    for b in ["Shimano", "Alexrims", "Schwalbe", "Batavus", "Centurion", "Busetto", "Ryde", "Liv", "Giant", "Innova", "Deli Tire", "Yosemite"]:
        if b.lower() in full_text.lower():
            brand = b
            break

    return wheel_size, mat, b_type, brand

def main():
    content = ALL_LISTINGS_PATH.read_text()
    sections = re.split(r"\n### `#(WF\d+|WRC\d+|WRS\d+|WS\d+|HB\d+|P\d+|T\d+)`", content)
    
    catalog = {}
    for i in range(1, len(sections), 2):
        item_id = sections[i]
        body = sections[i+1]
        
        # Asking price
        price_match = re.search(r"\* \*\*Asking Price:\*\* `?(\d+)", body)
        price = max(int(price_match.group(1)) if price_match else 100, 100)
        
        # Description
        desc_match = re.search(r"\*\*Marketplace Description:\*\*\s*\n\n>\s*(.*?)(?=\n\n\*\*Technical Specifications:\*\*|\n\n\*)", body, re.DOTALL)
        eng_desc = desc_match.group(1).replace("\n> ", " ").strip() if desc_match else ""
        
        # Specs
        specs_match = re.search(r"\*\*Technical Specifications:\*\*\s*\n\n(.*?)(?=\n\n\*\*Customer-Facing Listing Photos|\n\n---|\Z)", body, re.DOTALL)
        specs_text = specs_match.group(1).strip() if specs_match else ""
        specs_lines = [line.strip().lstrip("* ").strip() for line in specs_text.split("\n") if line.strip().startswith("*")]
        
        # Curated Photos
        photos = re.findall(r"!\[.*?\]\((/.*?)\)", body)
        # Filter existing
        valid_photos = [p for p in photos if Path(p).exists()]
        
        title = TITLES.get(item_id)
        if not title:
            # Fallback title formatting
            first_line = body.strip().split("\n")[0].lstrip("— ").strip()
            title = f"Bike Part / Cykeldel - {item_id} - Universal"

        full_desc = build_unified_description(item_id, title, eng_desc, specs_lines)
        w_size, mat, b_type, brand = get_fb_attributes(item_id, title, specs_lines)
        
        catalog[item_id] = {
            "item_id": item_id,
            "title": title,
            "price": price,
            "photos": valid_photos,
            "description": full_desc,
            "fb_wheel_size": w_size,
            "fb_material": mat,
            "fb_type": b_type,
            "fb_brand": brand,
            "tags": "cykel, cykeldel, bike parts, dba, kobenhavn, copenhagen, cykeludstyr"
        }

    OUTPUT_CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    print(f"Successfully generated catalog with {len(catalog)} items at {OUTPUT_CATALOG_PATH}")

if __name__ == "__main__":
    main()
