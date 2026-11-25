import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from import_lorcanaplayer import compare_catalog, existing_ids, main, parse_batch
from sync_lorcanaplayer import HEADERS, directory_rows, incremental_directory, make_directory_requests
from test_player_browser_import import batch, card


def compress(raw):
    return 'gzip:' + base64.b64encode(gzip.compress(raw.encode('utf-8'))).decode('ascii')


class BulkBrowserInputTests(unittest.TestCase):
    def test_compressed_batches_use_existing_card_validation(self):
        cards = [card(str(i), f'sample-{i}') for i in range(1, 401)]
        raw = batch(*cards)
        self.assertGreater(len(raw.encode('utf-8')), 60000)
        packed = compress(raw)
        self.assertLess(len(packed), 60000)
        records = parse_batch(packed)
        self.assertEqual(len(records), 400)
        self.assertEqual(records['400'][1], 'Challenge Set 3')
        with self.assertRaisesRegex(ValueError, 'repeated page ID'):
            parse_batch(compress(batch(card(), card())))

    def test_rejects_invalid_or_excessively_large_compressed_batch(self):
        with self.assertRaisesRegex(ValueError, 'Invalid compressed browser batch'):
            parse_batch('gzip:????')
        with self.assertRaisesRegex(ValueError, 'over 2 MB'):
            parse_batch(compress('x' * 2_000_001))

    def test_existing_ids_serialized_as_strings_and_sorted(self):
        a = parse_batch(batch(card('10', 'ten')))['10']
        b = parse_batch(batch(card('2', 'two')))['2']
        self.assertEqual(existing_ids([HEADERS, a, [], [2, *b[1:]]]),
                         {'version': 1, 'ids': ['2', '10']})
        with self.assertRaisesRegex(RuntimeError, 'Duplicate page ID'):
            existing_ids([HEADERS, a, a])

    def test_export_ids_never_writes_to_the_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['import_lorcanaplayer.py', '--spreadsheet-id', 'test',
                    '--export-ids', '--output', directory]
            service = Mock()
            with patch('sys.argv', args), patch('import_lorcanaplayer.connect', return_value=service), \
                    patch('import_lorcanaplayer.read_rows', return_value=[HEADERS]), patch('builtins.print'):
                main()
            exported = Path(directory, 'existing-ids.json').read_text(encoding='utf-8')
            self.assertEqual(json.loads(exported), {'version': 1, 'ids': []})
            service.batchUpdate.assert_not_called()

    def test_catalog_audit_compares_ids_even_when_totals_match(self):
        payload = {'version': 1, 'catalog': {
            'total': 2, 'ids': ['11', '12'], 'checked_at': '2026-11-24T17:00:00Z',
        }}
        rows = [HEADERS, parse_batch(batch(card('10', 'ten')))['10'],
                parse_batch(batch(card('11', 'eleven')))['11']]
        audit, report = compare_catalog(compress(json.dumps(payload)), rows)
        self.assertEqual(audit, {'checked_on': '2026-11-25', 'total': 2, 'sheet_only_count': 1})
        self.assertEqual(report['missing_ids'], ['12'])
        self.assertEqual(report['sheet_only_ids'], ['10'])
        payload['catalog']['ids'] = ['11', '11']
        with self.assertRaisesRegex(ValueError, 'unique page IDs'):
            compare_catalog(json.dumps(payload), rows)

    def test_footer_migration_preserves_highlight_snapshot_and_full_audit_date(self):
        rows = [HEADERS, ['11', 'Challenge Set 3', '5/C3']]
        old = directory_rows(rows, {'sets': {'Challenge Set 3': 1}, 'total': 1},
                             {'missing_ids': [], 'sheet_only_ids': []})
        old[3] = old[3] + ['lorcana-directory-changes-v1']
        next(row for row in old if row[0] == '最近完整性核查')[1] = '2026-11-17'
        updated = incremental_directory(rows, old)
        self.assertIn(['当前表内卡牌数', 1, '', ''], updated)
        self.assertIn(['最近完整性核查', '2026-11-17', '', ''], updated)
        self.assertTrue(any(row[0] == '最近目录更新' and row[1] for row in updated))
        meta = {'properties': {'sheetId': 7, 'gridProperties': {'rowCount': 100}}}
        requests = make_directory_requests(meta, updated, old)
        self.assertFalse(any('addConditionalFormatRule' in r for r in requests))
        self.assertEqual(next(r['setBasicFilter']['filter']['range']['endRowIndex']
                              for r in requests if 'setBasicFilter' in r), 5)

        audit = {'checked_on': '2026-11-25', 'total': 2, 'sheet_only_count': 0}
        newer = incremental_directory(rows, updated, audit)
        self.assertIn(['上次核查的网站卡牌数', 2, '', ''], newer)
        self.assertIn(['最近完整性核查', '2026-11-25', '', ''], newer)
        self.assertIsNone(incremental_directory(rows, newer, audit))
