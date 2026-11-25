import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from import_lorcanaplayer import apply_rows, audit_rows, main, parse_batch
from sync_lorcanaplayer import (
    DETAIL, DIRECTORY, DIRECTORY_TOTAL, HEADERS, directory_rows, incremental_directory,
    make_detail_requests,
)


def card(pid='10', slug='card-ten'):
    return {
        'id': pid, 'title': 'Example – Promo', 'url': f'https://lorcanaplayer.com/card/{slug}/',
        'image_url': 'https://lorcanaplayer.com/wp-content/uploads/example.jpg',
        'captured_on': '2026-11-25',
        'fields': {'Set': 'Challenge Set 3', 'Name': 'Example', 'Card Type': 'Action',
                   'Ink Cost': '0', 'Card Text': 'Draw a card. [Exert]'},
    }


def batch(*cards):
    return json.dumps({'version': 1, 'cards': list(cards)})


class BrowserImportTests(unittest.TestCase):
    def test_field_conversion_and_repeated_run(self):
        rows = [HEADERS]
        records = parse_batch(batch(card()))
        missing, report = audit_rows(rows, records)
        self.assertEqual(report['missing_ids'], ['10'])
        self.assertEqual(missing[0][8], 0)
        self.assertEqual(missing[0][16], 'Draw a card. [Exert]')
        self.assertEqual(missing[0][24], '2026-11-25')
        nothing, report = audit_rows(rows + missing, records)
        self.assertEqual(nothing, [])
        self.assertEqual(report['existing_count'], 1)
        self.assertFalse(report['directory_updated'])

    def test_refuses_unexpected_host_and_duplicate_records(self):
        with self.assertRaisesRegex(ValueError, 'invalid LorcanaPlayer detail URL'):
            parse_batch(batch({**card(), 'url': 'https://example.com/card/example/'}))
        with self.assertRaisesRegex(ValueError, 'repeated page ID or detail URL'):
            parse_batch(batch(card(), card('11')))
        with self.assertRaisesRegex(ValueError, 'repeated page ID or detail URL'):
            parse_batch(batch(card(), card('10', 'another-slug')))

    def test_link_and_id_must_agree_with_existing_sheet(self):
        record = parse_batch(batch(card()))['10']
        with self.assertRaisesRegex(RuntimeError, 'already in the sheet'):
            audit_rows([HEADERS, ['11', *record[1:]]], {'10': record})
        with self.assertRaisesRegex(RuntimeError, 'ID/URL mismatch'):
            audit_rows([HEADERS, record], parse_batch(batch(card(slug='another-slug'))))

    def test_detail_requests_retain_existing_rows_and_exclude_directory(self):
        earlier = parse_batch(batch(card('9', 'earlier')))['9']
        new = parse_batch(batch(card()))['10']
        detail = {
            'properties': {'sheetId': 7, 'gridProperties': {'rowCount': 2}},
            'basicFilter': {
                'range': {'sheetId': 7, 'startRowIndex': 0, 'endRowIndex': 2},
                'filterSpecs': [{'columnIndex': 0, 'filterCriteria': {'hiddenValues': ['9']}}],
            },
        }
        template = [{} for _ in HEADERS]
        template[22] = {'userEnteredFormat': {
            'textFormat': {'fontSize': 12, 'link': {'uri': earlier[22]}}
        }}
        requests, expected = make_detail_requests(detail, [HEADERS, earlier], [new], template)
        self.assertEqual(expected, [HEADERS, earlier, new])
        self.assertEqual(requests[0]['appendDimension']['length'], 1)
        self.assertNotIn('copyPaste', [key for request in requests for key in request])
        write = requests[1]['updateCells']
        self.assertEqual(write['start']['sheetId'], 7)
        self.assertEqual(write['start']['rowIndex'], 2)
        self.assertEqual(write['rows'][0]['values'][22]['userEnteredFormat']['textFormat'],
                         {'fontSize': 12, 'link': {'uri': new[22]}})
        self.assertEqual(requests[2]['setBasicFilter']['filter']['filterSpecs'],
                         detail['basicFilter']['filterSpecs'])

    def test_preview_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'batch.json'
            file.write_text(batch(card()), encoding='utf-8')
            args = ['import_lorcanaplayer.py', '--spreadsheet-id', 'test',
                    '--batch-file', str(file), '--output', directory]
            service = Mock()
            with patch('sys.argv', args), patch('import_lorcanaplayer.connect', return_value=service), \
                    patch('import_lorcanaplayer.read_rows', return_value=[HEADERS]), patch('builtins.print'):
                main()
            report = json.loads((Path(directory) / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['applied'])
            self.assertEqual(report['missing_ids'], ['10'])
            service.batchUpdate.assert_not_called()

    def test_apply_verifies_both_tabs_and_repeat_is_no_op(self):
        before = [HEADERS]
        added = [parse_batch(batch(card()))['10']]
        old_directory = directory_rows(
            before, {'sets': {'Challenge Set 3': 0}, 'total': 0},
            {'missing_ids': [], 'sheet_only_ids': []},
        )
        expected = incremental_directory(before + added, old_directory)
        service = Mock()
        service.get.return_value.execute.return_value = {'sheets': [
            {'properties': {'title': DETAIL, 'sheetId': 7, 'gridProperties': {'rowCount': 1}}},
            {'properties': {'title': DIRECTORY, 'sheetId': 8, 'gridProperties': {'rowCount': 100}}},
        ]}
        with patch('import_lorcanaplayer.read_rows', side_effect=[before, before + added]), \
                patch('import_lorcanaplayer.read_directory', side_effect=[old_directory, expected]):
            self.assertTrue(apply_rows(service, 'test', before, added))
        service.batchUpdate.assert_called_once()
        requests = service.batchUpdate.call_args.kwargs['body']['requests']
        self.assertIn(7, [r['updateCells']['start']['sheetId']
                          for r in requests if 'start' in r.get('updateCells', {})])
        rules = [r['addConditionalFormatRule']['rule'] for r in requests if 'addConditionalFormatRule' in r]
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]['ranges'][0]['sheetId'], 8)
        self.assertEqual(rules[0]['ranges'][0]['endRowIndex'], 6)
        service.reset_mock()
        self.assertFalse(apply_rows(service, 'test', before + added, []))
        service.batchUpdate.assert_not_called()

    def test_refresh_counts_without_claiming_a_new_site_audit(self):
        before = [
            HEADERS,
            ['1', 'The First Chapter', '1/204'],
            ['2', 'Challenge Set 3', '1/C3'],
            ['3', 'D23 Collection', '1/D23'],
        ]
        old_directory = directory_rows(
            before, {'sets': {'The First Chapter': 1, 'Challenge Set 3': 1, 'D23 Collection': 1}, 'total': 3},
            {'missing_ids': [], 'sheet_only_ids': []},
        )
        old_audit = next(row for row in old_directory if row[0] == '最近完整性核查')
        old_audit[1] = '2026-11-17'
        rows = before + [
            ['4', 'Challenge Set 3', '2/C3'],
            ['5', 'D23 Collection Challenge Set 3', '2/D23'],
            ['6', 'New Promo Set', '1/NEW'],
        ]
        refreshed = incremental_directory(rows, old_directory)
        counts = {row[1]: row[2] for row in refreshed[4:] if row[1]}
        self.assertEqual(counts['Challenge Set 3'], 3)
        self.assertEqual(counts['D23 Collection'], 2)
        self.assertEqual(counts[DIRECTORY_TOTAL], 6)
        self.assertEqual(refreshed[4], old_directory[4])
        self.assertIn(['NEW', 'New Promo Set', 1, '按当前明细统计。'], refreshed)
        self.assertIn(['上次核查的网站卡牌数', 3, '', ''], refreshed)
        self.assertIn(['本次合计变化', 3, '', ''], refreshed)
        self.assertIn(old_audit, refreshed)
        self.assertIsNone(incremental_directory(rows, refreshed))

    def test_refresh_never_touches_detail_cells(self):
        rows = [HEADERS, ['10', 'Challenge Set 3', '2/C3']]
        old_directory = directory_rows(
            [HEADERS], {'sets': {'Challenge Set 3': 0}, 'total': 0},
            {'missing_ids': [], 'sheet_only_ids': []},
        )
        expected = incremental_directory(rows, old_directory)
        service = Mock()
        service.get.return_value.execute.return_value = {'sheets': [
            {'properties': {'title': DIRECTORY, 'sheetId': 8, 'gridProperties': {'rowCount': 100}}},
        ]}
        with patch('import_lorcanaplayer.read_rows', return_value=rows), \
                patch('import_lorcanaplayer.read_directory', side_effect=[old_directory, expected]):
            self.assertTrue(apply_rows(service, 'test', rows, [], refresh_directory=True))
        requests = service.batchUpdate.call_args.kwargs['body']['requests']
        self.assertNotIn('copyPaste', [key for request in requests for key in request])
        self.assertEqual({p.get('start', p.get('range'))['sheetId']
                          for r in requests if (p := r.get('updateCells'))}, {8})

    def test_refuses_a_formula_in_a_visually_empty_target(self):
        service = Mock()
        service.get.return_value.execute.return_value = {'sheets': [
            {'properties': {'title': DETAIL, 'sheetId': 7, 'gridProperties': {'rowCount': 2}}}
        ]}
        service.values.return_value.batchGet.return_value.execute.return_value = {
            'valueRanges': [{'values': [['=""']]}],
        }
        rows = [HEADERS]
        with patch('import_lorcanaplayer.read_rows', return_value=rows):
            with self.assertRaisesRegex(RuntimeError, 'existing data or a formula'):
                apply_rows(service, 'test', rows, [parse_batch(batch(card()))['10']])
        service.batchUpdate.assert_not_called()
