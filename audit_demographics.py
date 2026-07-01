#!/usr/bin/env python3
"""
Premium demographic audit: individually analyze each profile using GPT-4o
across all 8 demographic categories. Uses BRAND INPUT and BRAND CATEGORY
from the CSV to properly identify each subject.
"""

import boto3, csv, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

s3 = boto3.client('s3', region_name='us-east-2')
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
BUCKET = 'dashboard-inputs'
MULT = 329_900_000 / 10_000_000

DEMO_CATS = [
    'AGE', 'GENDER', 'ETHNICITY', 'EDUCATION', 'INCOME',
    'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'RELATIONSHIP'
]

SKIP_NAMES = [
    'MARK_ROBER', 'SYSTEM_OF_A_DOWN', 'DEFTONES', 'METALLICA',
    'LINKIN_PARK', 'JAKE_PAUL', 'GEN_POP', 'GEN POP'
]


def pf(v):
    if not v:
        return 0.0
    s = str(v).strip().replace('%', '').replace(',', '')
    try:
        return float(s)
    except Exception:
        return 0.0


def clean_subject_name(brand_input_val):
    """Extract clean subject name from the BRAND INPUT field."""
    if not brand_input_val:
        return ''
    first = brand_input_val.split(',')[0].strip()
    name = first.replace('-', ' ').replace('_', ' ').replace('.', ' ')
    name = name.replace('%20', ' ').replace('+', ' ')
    parts = name.split()
    return ' '.join(p.capitalize() for p in parts if p)


def get_shares(rows, cat):
    """Get relative distribution (shares summing to ~100) for a category."""
    items = []
    for i, r in enumerate(rows):
        if (r.get('Column', '') or '').upper().strip() == cat:
            val = (r.get('Value', '') or '').strip()
            bp = pf(r.get('Brand Penetration (Row)', ''))
            items.append((val, bp, i))
    if not items:
        return {}, []
    total = sum(bp for _, bp, _ in items)
    if total <= 0:
        return {}, []
    shares = {}
    for val, bp, idx in items:
        shares[val] = round(bp / total * 100, 2)
    return shares, items


def process_file(f):
    short_name = f.replace('.csv', '')
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f)
        txt = obj['Body'].read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(txt))
        rows = list(reader)
        fieldnames = reader.fieldnames

        bc = ''
        brand_input = ''
        sample_raw = 0
        for r in rows:
            col = (r.get('Column', '') or '').upper().strip()
            if col == 'BRAND CATEGORY':
                bc = (r.get('Value', '') or '').strip()
            if col == 'BRAND INPUT':
                brand_input = (r.get('Value', '') or '').strip()
            if col == 'SAMPLE SIZE':
                try:
                    sample_raw = int(float(
                        str(r.get('Original Raw Numbers', '')).replace(',', '')
                    ))
                except Exception:
                    pass

        subject = clean_subject_name(brand_input)
        if not subject:
            subject = short_name

        if 'SERIES' in bc.upper():
            return ('SKIP', subject, f'SERIES ({bc})', 0)

        all_shares = {}
        all_items = {}
        has_data = False
        for cat in DEMO_CATS:
            shares, items = get_shares(rows, cat)
            if shares:
                all_shares[cat] = shares
                all_items[cat] = items
                has_data = True

        if not has_data:
            return ('SKIP', subject, 'no demographic data', 0)

        demo_block = ""
        key_block = ""
        for cat in DEMO_CATS:
            if cat in all_shares:
                demo_block += f"- {cat}: {json.dumps(all_shares[cat])}\n"
                keys = list(all_shares[cat].keys())
                key_block += f"- {cat} values: {json.dumps(keys)}\n"

        prompt = f"""You are a senior US consumer insights analyst. Determine the PRECISE digital audience demographics for the subject below.

SUBJECT: "{subject}"
CATEGORY: {bc}
(The filename timestamp is irrelevant — focus only on the subject name and category above.)

STEP 1 — IDENTIFY THE SUBJECT:
Based on the name "{subject}" and category "{bc}", determine exactly what this is. Is it a specific person, brand, product, platform, team, event, restaurant, retailer, etc.? If it's a person, who are they — what are they known for, what is their race/ethnicity, age, and what kind of content or work do they produce?

STEP 2 — WHO IS THE ACTUAL DIGITAL CONSUMER?
Think carefully about who digitally interacts with "{subject}" in the US:

For PEOPLE: Their fanbase reflects their identity and content. A Black rapper has higher Black audience share. A 50-year-old white male comedian's audience skews older, white, male. A young Latina actress's audience includes significant Latino representation. A Gen Z TikToker's audience is very young.

For BRANDS/PRODUCTS: Who actually buys and uses this? Consider price point, distribution, marketing. Men's grooming = mostly male. Premium skincare = older women with income. Fast food = diverse, younger, lower-middle income. Luxury cars = older, affluent, educated.

For PLATFORMS/MEDIA: Who uses this specific service? A streaming platform's audience depends on its content library. A social media app's audience depends on its user demographics.

HARD CONSTRAINTS:
- Financial products (banks, credit cards, insurance, investments): Under 18 combined must be <5%. Adults 30+ dominate.
- Alcohol/tobacco/cannabis: ZERO under 21. Period.
- Gendered products (men's underwear, women's beauty, etc.): Must skew 75%+ toward target gender.
- US general population baseline: ~58% White, ~19% Latino, ~13% Black, ~6% Asian, ~4% Other. Mass-market brands approximate this unless there's a specific reason.
- US LGBTQ+ identification: ~7-8% overall. Higher (~20%) among Gen Z. Adjust for audience age and whether subject has specific LGBTQ+ appeal.
- US education: ~37% bachelor's or higher. Premium brands skew educated. Blue-collar brands less so.
- Income should match the product's price point and target market.
- Parental status: ~40% of US adults have kids under 18. Family-oriented brands higher. Youth-oriented brands lower.

STEP 3 — COMPARE TO CURRENT DATA:
Current demographic shares for "{subject}" (percentages within each category, summing to ~100%):
{demo_block}
Flag anything off by more than 3 percentage points from what you'd expect.

STEP 4 — VERDICT:
Use EXACTLY these value labels:
{key_block}
If accurate: {{"status": "OK", "notes": "brief reason"}}

If corrections needed:
{{"status": "FIX", "notes": "1-2 sentences on what's wrong", "corrections": {{
  "CATEGORY_NAME": {{"label1": number, "label2": number, ...}},
  ...
}}}}

RULES:
- Only include categories needing correction.
- Each corrected category MUST sum to 100.
- Be PRECISE: use specific numbers like 23, 37, 14 — not rounded to 5s.
- Return ONLY valid JSON. No markdown, no commentary."""

        resp_ai = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.15,
            max_tokens=1500
        )
        text = resp_ai.choices[0].message.content.strip()

        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        depth = 0
        end = 0
        for i, c in enumerate(text):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            text = text[:end]

        result = json.loads(text)

        if result.get('status') == 'FIX' and 'corrections' in result:
            corr = result['corrections']
            changes = 0

            for cat_name, new_shares in corr.items():
                cat_upper = cat_name.upper()
                if not isinstance(new_shares, dict):
                    continue
                if cat_upper not in all_items:
                    continue

                items = all_items[cat_upper]
                total_bp = sum(bp for _, bp, _ in items)
                if total_bp <= 0:
                    continue

                indices_map = {}
                for val, bp, idx in items:
                    indices_map[val.upper()] = idx

                matched = 0
                for label, new_pct in new_shares.items():
                    if label.strip().upper() in indices_map:
                        matched += 1
                if matched == 0:
                    continue

                for label, new_pct in new_shares.items():
                    key = label.strip().upper()
                    if key not in indices_map:
                        continue
                    idx = indices_map[key]
                    new_bp = float(new_pct) * total_bp / 100.0
                    rows[idx]['Brand Penetration (Row)'] = f'{new_bp:.4f}%'
                    new_raw = round(sample_raw * new_bp / 100.0)
                    rows[idx]['Original Raw Numbers'] = str(new_raw)
                    rows[idx]['US Gen Pop Projection'] = str(
                        int(round(new_raw * MULT))
                    )
                    changes += 1

                all_indices = [idx for _, _, idx in items]
                new_total_bp = sum(
                    pf(rows[ix].get('Brand Penetration (Row)', ''))
                    for ix in all_indices
                )
                if new_total_bp > 0:
                    for ix in all_indices:
                        bp = pf(rows[ix].get('Brand Penetration (Row)', ''))
                        rows[ix]['Category Share'] = \
                            f"{bp / new_total_bp * 100.0:.4f}%"

            if changes > 0:
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
                s3.put_object(
                    Bucket=BUCKET, Key=f,
                    Body=buf.getvalue().encode('utf-8-sig')
                )
                return (
                    'FIXED', subject,
                    f"{result.get('notes', '')[:120]}",
                    changes
                )
            else:
                return ('OK', subject, 'AI flagged but no labels matched', 0)
        else:
            return ('OK', subject, result.get('notes', '')[:120], 0)

    except json.JSONDecodeError as e:
        return ('ERROR', subject if 'subject' in dir() else short_name,
                f'JSON parse: {str(e)[:80]}', 0)
    except Exception as e:
        return ('ERROR', short_name, str(e)[:100], 0)


def main():
    letters = sys.argv[1] if len(sys.argv) > 1 else \
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    resp = s3.list_objects_v2(Bucket=BUCKET, Delimiter='/')
    all_files = sorted([
        o['Key'] for o in resp.get('Contents', [])
        if o['Key'].lower().endswith('.csv')
    ])

    filtered = [
        f for f in all_files
        if not any(s in f.upper() for s in SKIP_NAMES)
        and f[0].upper() in letters.upper()
    ]

    print(f"\n{'='*70}")
    print(f"  PREMIUM DEMOGRAPHIC AUDIT — GPT-4o — ALL 8 CATEGORIES")
    print(f"  Subject identified from BRAND INPUT field")
    print(f"  Letters: {letters}")
    print(f"  Files to process: {len(filtered)} (workers: {workers})")
    print(f"{'='*70}\n", flush=True)

    totals = {'FIXED': 0, 'OK': 0, 'SKIP': 0, 'ERROR': 0}
    start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_file, f): f for f in filtered}
        done = 0
        for future in as_completed(futures):
            status, name, notes, changes = future.result()
            done += 1
            totals[status] = totals.get(status, 0) + 1

            if status == 'FIXED':
                print(
                    f"  [{done}/{len(filtered)}] FIXED: {name} "
                    f"({changes} vals) — {notes}",
                    flush=True
                )
            elif status == 'ERROR':
                print(
                    f"  [{done}/{len(filtered)}] ERROR: {name} — {notes}",
                    flush=True
                )
            elif status == 'SKIP':
                print(
                    f"  [{done}/{len(filtered)}] SKIP: {name} — {notes}",
                    flush=True
                )
            else:
                print(
                    f"  [{done}/{len(filtered)}] OK: {name} — {notes}",
                    flush=True
                )

    elapsed = time.time() - start
    print(f"\n{'='*70}")
    print(f"  AUDIT COMPLETE in {elapsed:.0f}s")
    print(f"  Fixed={totals['FIXED']}, OK={totals['OK']}, "
          f"Skipped={totals['SKIP']}, Errors={totals['ERROR']}")
    print(f"{'='*70}\n", flush=True)


if __name__ == '__main__':
    main()
