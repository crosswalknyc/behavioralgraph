#!/usr/bin/env python3
"""Every product CSV download carries its download date and study
window (2026-09-28 Jenna: "date downloaded: todays date... study date
range: jan 1 2025 - dec 31 2025 or whatever it is").

Stamped at SERVE time on the download routes (stored files keep
receiving silent in-place corrections, so a frozen download date
belongs to the moment of download): Profile IQ / Subscriber IQ files
via download-cached, ticket sales trackers, legacy job downloads, SF
Conversion files, and Prometheus data exports (stamped at write since
they serve via presigned links). The study range parses from the
file's own window row. Replayable string patch."""
import sys
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
src = APP.read_text(encoding="utf-8")

if "_stamp_csv_text" in src:
    print("already applied")
    sys.exit(0)

# ---- helper, ahead of the first user ----------------------------------
OLD_DL = '''def download_cached(s3_key):
    """Download a cached file from S3."""
    ok, err = _require_profile_run_access(s3_key)
    if not ok:
        return err
    if not s3_client:
        return jsonify({'error': 'S3 not configured'}), 500

    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read()
        
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={s3_key}'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500'''

NEW_DL = '''def _fmt_study_date(iso):
    try:
        return datetime.strptime(str(iso), '%Y-%m-%d').strftime('%B %-d, %Y')
    except Exception:
        return str(iso)


def _stamp_csv_text(text, study_range=''):
    """Prepend DATE DOWNLOADED + STUDY DATE RANGE rows to a CSV
    (2026-09-28 Jenna: every product CSV names when it was pulled and
    the window it covers). The range parses from the file's own window
    row when the caller does not supply one. Already-stamped text
    passes through untouched; any failure returns the original."""
    try:
        lines = str(text).splitlines()
        if not lines:
            return text
        if any(ln.startswith('DATE DOWNLOADED') for ln in lines[:4]):
            return text
        rng = str(study_range or '').strip()
        if not rng:
            m = re.search(
                r'(\\d{4}-\\d{2}-\\d{2})\\s*(?:TO|to|To|through|-|\\u2013)'
                r'\\s*(\\d{4}-\\d{2}-\\d{2})', text)
            if m:
                rng = (f"{_fmt_study_date(m.group(1))} - "
                       f"{_fmt_study_date(m.group(2))}")
        today = datetime.now(timezone.utc).strftime('%B %-d, %Y')
        ncols = max(1, lines[0].count(',') + 1)

        def _row(label, val):
            cell = '"' + str(val).replace('"', '""') + '"'
            return ','.join([label, cell] + [''] * max(0, ncols - 2))

        out = [lines[0],
               _row('DATE DOWNLOADED', today),
               _row('STUDY DATE RANGE', rng or 'Not stated in file')]
        return '\\n'.join(out + lines[1:])
    except Exception:
        return text


def _stamp_csv_bytes(content, study_range=''):
    try:
        return _stamp_csv_text(
            content.decode('utf-8', errors='replace'),
            study_range).encode('utf-8')
    except Exception:
        return content


def download_cached(s3_key):
    """Download a cached file from S3."""
    ok, err = _require_profile_run_access(s3_key)
    if not ok:
        return err
    if not s3_client:
        return jsonify({'error': 'S3 not configured'}), 500

    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read()
        if str(s3_key).lower().endswith('.csv'):
            csv_content = _stamp_csv_bytes(csv_content)
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={s3_key}'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500'''

count = src.count(OLD_DL)
if count != 1:
    raise RuntimeError(f"download_cached anchor found {count}x")
src = src.replace(OLD_DL, NEW_DL)

# ---- ticket sales tracker ---------------------------------------------
OLD_TST = """        response = s3_client.get_object(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read()
        return Response(csv_content, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={s3_key.split("/")[-1]}'})"""
NEW_TST = """        response = s3_client.get_object(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Key=s3_key)
        csv_content = _stamp_csv_bytes(response['Body'].read())
        return Response(csv_content, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={s3_key.split("/")[-1]}'})"""
count = src.count(OLD_TST)
if count != 1:
    raise RuntimeError(f"tracker anchor found {count}x")
src = src.replace(OLD_TST, NEW_TST)

# ---- SF-LF conversion ---------------------------------------------------
OLD_SF = """        response = s3_client.get_object(Bucket=SF_LF_CONV_S3_BUCKET, Key=s3_key)
        content = response['Body'].read()
        filename = os.path.basename(s3_key)
        return Response(
            content,
            mimetype='text/csv',"""
NEW_SF = """        response = s3_client.get_object(Bucket=SF_LF_CONV_S3_BUCKET, Key=s3_key)
        content = _stamp_csv_bytes(response['Body'].read())
        filename = os.path.basename(s3_key)
        return Response(
            content,
            mimetype='text/csv',"""
count = src.count(OLD_SF)
if count != 1:
    raise RuntimeError(f"sf-lf anchor found {count}x")
src = src.replace(OLD_SF, NEW_SF)

# ---- legacy job download: local file + S3 fallback ----------------------
OLD_JOB = """    result_file = job.get('result_file')
    if result_file and os.path.exists(result_file):
        return send_file(
            result_file,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"{job.get('project_name', 'data')}_behavioral_graph.csv"
        )"""
NEW_JOB = """    result_file = job.get('result_file')
    if result_file and os.path.exists(result_file):
        from io import BytesIO
        with open(result_file, 'rb') as _fh:
            _stamped = _stamp_csv_bytes(_fh.read())
        return send_file(
            BytesIO(_stamped),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"{job.get('project_name', 'data')}_behavioral_graph.csv"
        )"""
count = src.count(OLD_JOB)
if count != 1:
    raise RuntimeError(f"job local anchor found {count}x")
src = src.replace(OLD_JOB, NEW_JOB)

OLD_JOB_S3 = """            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            from io import BytesIO
            body = response['Body'].read()
            return send_file(
                BytesIO(body),"""
NEW_JOB_S3 = """            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            from io import BytesIO
            body = _stamp_csv_bytes(response['Body'].read())
            return send_file(
                BytesIO(body),"""
count = src.count(OLD_JOB_S3)
if count != 1:
    raise RuntimeError(f"job s3 anchor found {count}x")
src = src.replace(OLD_JOB_S3, NEW_JOB_S3)

# ---- Prometheus data export: stamp at write (served presigned) ----------
OLD_PM = """        fname, csv_text = pma.build_generated_csv(entry)
        fname = _pm_csv_task_filename(entry) or fname"""
NEW_PM = """        fname, csv_text = pma.build_generated_csv(entry)
        _rng = ''
        try:
            if entry.get('ws') and entry.get('we'):
                _rng = (f"{_fmt_study_date(entry['ws'])} - "
                        f"{_fmt_study_date(entry['we'])}")
            elif entry.get('wl'):
                _rng = str(entry['wl'])
        except Exception:
            _rng = ''
        csv_text = _stamp_csv_text(csv_text, _rng)
        fname = _pm_csv_task_filename(entry) or fname"""
count = src.count(OLD_PM)
if count != 1:
    raise RuntimeError(f"pm export anchor found {count}x")
src = src.replace(OLD_PM, NEW_PM)

APP.write_text(src, encoding="utf-8")
print("csv stamp patch applied (6 sites)")
