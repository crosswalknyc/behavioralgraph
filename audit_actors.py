#!/usr/bin/env python3
"""
Actor-specific premium demographic audit using GPT-4o.
Fixes the systematic issues where actor profiles default to young+female.
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
    if not brand_input_val:
        return ''
    first = brand_input_val.split(',')[0].strip()
    name = first.replace('-', ' ').replace('_', ' ').replace('.', ' ')
    name = name.replace('%20', ' ').replace('+', ' ')
    parts = name.split()
    return ' '.join(p.capitalize() for p in parts if p)


def get_shares(rows, cat):
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

        subject = clean_subject_name(brand_input) or short_name

        all_shares = {}
        all_items = {}
        for cat in DEMO_CATS:
            shares, items = get_shares(rows, cat)
            if shares:
                all_shares[cat] = shares
                all_items[cat] = items

        if not all_shares:
            return ('SKIP', subject, 'no demographic data', 0)

        demo_block = ""
        key_block = ""
        for cat in DEMO_CATS:
            if cat in all_shares:
                demo_block += f"- {cat}: {json.dumps(all_shares[cat])}\n"
                keys = list(all_shares[cat].keys())
                key_block += f"- {cat} values: {json.dumps(keys)}\n"

        prompt = f"""You are a senior US entertainment audience demographics expert. Determine PRECISE audience demographics for this actor/actress.

SUBJECT: "{subject}"
CATEGORY: {bc}

STEP 1 — IDENTIFY THIS PERSON:
Who is "{subject}"? Determine their:
- Gender, race/ethnicity, approximate age
- What they are most known for (specific roles, shows, movies, genres)
- Career peak era and current relevance
- Whether they are openly LGBTQ+, or known for playing iconic LGBTQ+ characters
- Whether they are considered a heartthrob, sex symbol, or have a fanbase that skews toward a particular gender

STEP 2 — DETERMINE THEIR SPECIFIC DIGITAL AUDIENCE:
Think about who ACTUALLY follows and digitally engages with THIS specific person. Every actor is different — do NOT apply generic rules. Reason about this individual:

GENDER — depends entirely on the person:
- Most male actors have roughly balanced audiences (48-55% male). But a male heartthrob or romantic lead (Timothée Chalamet, young Brad Pitt, Harry Styles type) can have 55-65% female audiences.
- Male comedians doing stand-up often skew male (52-58%).
- Female actresses typically skew 55-65% female, but action/superhero stars can be closer to 50/50.
- Think about THIS person's specific appeal and content.

ETHNICITY — must reflect the actor's identity and content:
- A Black actor's audience has significantly higher Black representation than the US baseline of 13%. How much higher depends on their content: mainstream crossover (28-38%), primarily Black-audience content (40-55%), or general (25-35%).
- The White percentage for a non-White actor should be LOWER than the US average of ~58%.
- Latino, Asian, and other non-White actors similarly draw elevated same-ethnicity audiences.
- White actors in mainstream content: typically 55-65% White.

AGE — depends on the person's career and content:
- Under 16 is typically very low (<4%) but CAN be higher if the actor is in children's or family content (Disney stars, Nickelodeon actors, etc.).
- 16-18 can be higher for actors in teen content (Stranger Things cast, teen dramas).
- Age distribution correlates with career peak: a 90s star draws 40-60+ audiences; a current young star draws 18-30s.

SEXUAL ORIENTATION — depends on the person:
- Openly LGBTQ+ actors: 18-30% YES depending on how central that is to their public identity.
- Actors famous for playing iconic LGBTQ+ characters (e.g., someone who played a beloved lesbian/gay character for multiple seasons): 15-25% YES even if the actor is straight, because the LGBTQ+ community deeply engages with that content.
- Otherwise, US baseline is ~7-8%.

INCOME/EDUCATION — depends on the content type:
- Prestige TV/indie film audiences: slightly more educated, moderate-high income.
- Blockbuster/action: middle income, average education.

PARENTAL STATUS/RELATIONSHIP — consider the audience age and content:
- Older audiences = more likely married with kids.
- Young audiences = more single, fewer kids.

STEP 3 — EVALUATE CURRENT DATA:
Current demographic shares for "{subject}":
{demo_block}
Flag anything off by more than 3 percentage points.

STEP 4 — VERDICT:
Use EXACTLY these value labels:
{key_block}
If accurate: {{"status": "OK", "notes": "brief reason"}}

If corrections needed:
{{"status": "FIX", "notes": "what's wrong", "corrections": {{
  "CATEGORY_NAME": {{"label1": number, ...}},
  ...
}}}}

RULES:
- Only include categories needing correction.
- Each corrected category MUST sum to 100.
- Be PRECISE: use specific numbers, not rounded to 5s.
- Return ONLY valid JSON. No markdown."""

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
                for label in new_shares:
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
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4

    resp = s3.list_objects_v2(Bucket=BUCKET, Delimiter='/')
    all_files = sorted([
        o['Key'] for o in resp.get('Contents', [])
        if o['Key'].lower().endswith('.csv')
    ])
    filtered = [
        f for f in all_files
        if not any(s in f.upper() for s in SKIP_NAMES)
    ]

    print("Scanning for ACTOR/ACTRESS profiles...", flush=True)
    actor_files = []
    for f in filtered:
        obj = s3.get_object(Bucket=BUCKET, Key=f)
        txt = obj['Body'].read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(txt))
        for r in reader:
            if (r.get('Column', '') or '').upper().strip() == 'BRAND CATEGORY':
                bc_val = (r.get('Value', '') or '').upper().strip()
                if 'ACTOR' in bc_val or 'ACTRESS' in bc_val:
                    actor_files.append(f)
                break

    print(f"\n{'='*70}")
    print(f"  ACTOR/ACTRESS DEMOGRAPHIC AUDIT — GPT-4o")
    print(f"  Files to process: {len(actor_files)} (workers: {workers})")
    print(f"{'='*70}\n", flush=True)

    totals = {'FIXED': 0, 'OK': 0, 'SKIP': 0, 'ERROR': 0}
    start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_file, f): f for f in actor_files}
        done = 0
        for future in as_completed(futures):
            status, name, notes, changes = future.result()
            done += 1
            totals[status] = totals.get(status, 0) + 1

            if status == 'FIXED':
                print(
                    f"  [{done}/{len(actor_files)}] FIXED: {name} "
                    f"({changes} vals) — {notes}",
                    flush=True
                )
            elif status == 'ERROR':
                print(
                    f"  [{done}/{len(actor_files)}] ERROR: {name} — {notes}",
                    flush=True
                )
            else:
                print(
                    f"  [{done}/{len(actor_files)}] OK: {name} — {notes}",
                    flush=True
                )

    elapsed = time.time() - start
    print(f"\n{'='*70}")
    print(f"  ACTOR AUDIT COMPLETE in {elapsed:.0f}s")
    print(f"  Fixed={totals['FIXED']}, OK={totals['OK']}, "
          f"Errors={totals['ERROR']}")
    print(f"{'='*70}\n", flush=True)


if __name__ == '__main__':
    main()
