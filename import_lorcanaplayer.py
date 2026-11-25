"""Append a batch of LorcanaPlayer cards captured in a browser."""
import argparse
import base64
import binascii
import gzip
from io import BytesIO
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import zlib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sheets_sync import connect
from sync_lorcanaplayer import (
    DETAIL, HEADERS, build_detail_row, compare, make_detail_requests, plan_rows,
    read_rows,
)


def source_url(value, path_pattern):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return False
    url = urlsplit(value)
    return (
        url.scheme == 'https' and url.netloc == 'lorcanaplayer.com'
        and not url.query and not url.fragment
        and re.fullmatch(path_pattern, url.path) is not None
    )


def parse_batch(raw):
    if len(raw.encode('utf-8')) > 60000:
        raise ValueError('Browser batch is over 60 KB; split it into smaller batches')
    if raw.startswith('gzip:'):
        try:
            compressed = base64.b64decode(raw[5:], validate=True)
            with gzip.GzipFile(fileobj=BytesIO(compressed)) as stream:
                raw = stream.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('Unpacked browser batch is over 2 MB')
            raw = raw.decode('utf-8')
        except (binascii.Error, OSError, EOFError, UnicodeError, zlib.error) as exc:
            raise ValueError('Invalid compressed browser batch') from exc
    batch = json.loads(raw)
    if (not isinstance(batch, dict) or type(batch.get('version')) is not int
            or batch['version'] != 1):
        raise ValueError('Expected a LorcanaPlayer browser batch with version 1')
    cards = batch.get('cards')
    if not isinstance(cards, list) or not cards:
        raise ValueError('Browser batch must contain at least one card')

    records = {}
    urls = set()
    for index, card in enumerate(cards, 1):
        if not isinstance(card, dict):
            raise ValueError(f'Card {index}: expected an object')
        pid = card.get('id')
        if not isinstance(pid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', pid):
            raise ValueError(f'Card {index}: invalid LorcanaPlayer page ID')
        url = card.get('url')
        if not source_url(url, r'/card/[^/]+/?'):
            raise ValueError(f'Card {index}: invalid LorcanaPlayer detail URL')
        if pid in records or url.rstrip('/') in urls:
            raise ValueError(f'Card {index}: repeated page ID or detail URL')
        title = card.get('title')
        fields = card.get('fields')
        if (not isinstance(title, str) or not title.strip() or not isinstance(fields, dict)
                or any(not isinstance(value, str) for value in fields.values())
                or any(not fields.get(k, '').strip() for k in ('Name', 'Set', 'Card Type'))):
            raise ValueError(f'Card {index}: missing title, name, set or card type')
        image_url = card.get('image_url', '')
        if image_url != '' and not source_url(image_url, r'/wp-content/uploads/[^?#]+'):
            raise ValueError(f'Card {index}: invalid LorcanaPlayer image URL')
        captured_on = card.get('captured_on')
        if not isinstance(captured_on, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', captured_on):
            raise ValueError(f'Card {index}: expected capture date YYYY-MM-DD')
        date.fromisoformat(captured_on)
        records[pid] = build_detail_row(
            pid, url, title, fields, image_url, captured_on, 'LorcanaPlayer 浏览器增量补录',
        )
        urls.add(url.rstrip('/'))
    return records


def audit_rows(rows, records):
    submitted = {'cards': {pid: {'url': row[22]} for pid, row in records.items()}}
    missing_ids, comparison = compare(rows, submitted)
    imported_by_url = {row[22].rstrip('/'): pid for pid, row in records.items()}
    for row in rows[1:]:
        if row and row[0]:
            submitted_id = imported_by_url.get(str(row[22]).rstrip('/'))
            if submitted_id is not None and submitted_id != str(row[0]):
                raise RuntimeError(f'Detail URL is already in the sheet under page ID {row[0]}')

    missing = [records[pid] for pid in missing_ids]
    return missing, {
        'mode': 'browser_batch',
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'batch_count': len(records),
        'sheet_count_before': comparison['sheet_count'],
        'existing_count': len(records) - len(missing),
        'missing_ids': missing_ids,
        'missing_cards': [
            {'id': row[0], 'name': row[3], 'set': row[1]} for row in missing
        ],
        'directory_updated': False,
        'applied': False,
        'rows_added': 0,
    }


def apply_rows(service, spreadsheet_id, before, missing):
    if not missing:
        return
    if read_rows(service, spreadsheet_id) != before:
        raise RuntimeError('Sheet changed during audit; rerun to refresh the preview')
    properties = service.get(spreadsheetId=spreadsheet_id, includeGridData=False).execute()
    detail = next(s for s in properties['sheets'] if s['properties']['title'] == DETAIL)
    last_card = max((i for i, row in enumerate(before[1:], 2) if row and row[0]), default=0)
    exemplar = []
    if last_card:
        source = service.get(
            spreadsheetId=spreadsheet_id, ranges=[f"'{DETAIL}'!A{last_card}:Z{last_card}"],
            includeGridData=True,
        ).execute()
        exemplar = source['sheets'][0]['data'][0]['rowData'][0]['values']
    requests, expected = make_detail_requests(detail, before, missing, exemplar)

    # A formula can render as an empty cell. Check any destination already on the grid
    # with FORMULA rendering before copying format or writing values.
    _, writes = plan_rows(before, missing)
    limit = detail['properties']['gridProperties']['rowCount']
    ranges = [f"'{DETAIL}'!A{i + 1}:Z{i + 1}" for i, _ in writes if i < limit]
    if ranges:
        dest = service.values().batchGet(
            spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption='FORMULA',
        ).execute()
        for area in dest.get('valueRanges', []):
            if any(value not in ('', None) for row in area.get('values', []) for value in row):
                raise RuntimeError('A target row contains existing data or a formula; review before writing')
    service.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': requests}).execute(num_retries=0)
    actual = read_rows(service, spreadsheet_id)

    def normalize(rows):
        return [[value if value is not None else '' for value in row]
                + [''] * (len(HEADERS) - len(row)) for row in rows]

    if normalize(actual) != normalize(expected):
        raise RuntimeError('Detail write verification failed; inspect the sheet before retrying')


def existing_ids(rows):
    compare(rows, {'cards': {}})
    ids = sorted((str(row[0]) for row in rows[1:] if row and row[0]), key=int)
    return {'version': 1, 'ids': ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-file', type=Path, help='Browser JSON file (instead of LORCANAPLAYER_BATCH_JSON)')
    parser.add_argument('--spreadsheet-id', default=os.getenv('SPREADSHEET_ID', ''))
    parser.add_argument('--output', type=Path, default=Path('output/player'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--export-ids', action='store_true')
    args = parser.parse_args()
    if not args.spreadsheet_id:
        parser.error('Set SPREADSHEET_ID')
    if args.export_ids:
        service = connect()
        index = existing_ids(read_rows(service, args.spreadsheet_id))
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'existing-ids.json').write_text(
            json.dumps(index, separators=(',', ':')) + '\n', encoding='utf-8',
        )
        print(f"Exported {len(index['ids'])} existing LorcanaPlayer page IDs", flush=True)
        return
    try:
        raw = args.batch_file.read_text(encoding='utf-8-sig') if args.batch_file else os.getenv('LORCANAPLAYER_BATCH_JSON', '')
        records = parse_batch(raw)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    service = connect()
    rows = read_rows(service, args.spreadsheet_id)
    missing, report = audit_rows(rows, records)
    if args.apply:
        apply_rows(service, args.spreadsheet_id, rows, missing)
        report.update(applied=True, rows_added=len(missing))

    args.output.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    (args.output / 'report.json').write_text(report_json + '\n', encoding='utf-8')
    print(report_json, flush=True)


if __name__ == '__main__':
    main()
