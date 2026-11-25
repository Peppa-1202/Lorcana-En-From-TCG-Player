import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from import_lorcanaplayer import existing_ids, main, parse_batch
from sync_lorcanaplayer import HEADERS
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
