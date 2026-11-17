import json
from pathlib import Path
import unittest
from promo_ocr import candidates, consensus, subtype, PENDING, PROMO
from sheets_sync import build_requests

class PromoTests(unittest.TestCase):
    def test_formats_and_languages(self):
        for text,want in [('20 / C2 - EN - 10','20/C2·EN·10'),('24B/P2 • EN • 7','24B/P2·EN·7'),('1/CC1.EN.1','1/CC1·EN·1'),('25/P1·ZH·1','25/P1·ZH·1')]:
            self.assertEqual(candidates(text),{want})
    def test_conflict_and_single_read_are_pending(self):
        self.assertEqual(consensus(['20/C2·EN·10','20/C3·EN·10']),PENDING)
        self.assertEqual(consensus(['20/C2·EN·10','unreadable']),PENDING)
        self.assertEqual(consensus(['20/C2·EN·10']*2),'20/C2·EN·10')
    def test_combination_count_and_unnumbered(self):
        self.assertEqual(subtype('13/P3·EN·6;14/P3·EN·6'),'P3')
        self.assertEqual(subtype('13/P3·EN·6;14/P2·EN·6'),PENDING)
        self.assertEqual(subtype('JA·14'),'无编号')
    def test_reviewed_entries_valid(self):
        data=json.loads(Path('promo_codes_reviewed.json').read_text())
        for entry in data.values():
            self.assertNotEqual(subtype(entry['code']),PENDING)
            self.assertEqual(len(entry['sha256']),64)
    def test_migration_and_repeat_directory(self):
        meta={'sheets':[{'properties':{'title':'洛卡纳TCG','sheetId':1,'gridProperties':{'rowCount':3,'columnCount':26}}},{'properties':{'title':'洛卡纳TCG目录','sheetId':2}}]}
        rows=[['header'],['1',PROMO,'DLPC'],['2',PROMO,'DLPC']]
        promo={'1':{'code':'24A/P2·EN·7','source':'test'},'2':{'code':PENDING,'source':'test'}}
        directory=[[],[],[],[],['DLPC',PROMO,2],['','合计',2],[],['最近完整性核查','old']]
        requests=build_requests(meta,rows,[],directory,[],promo)
        self.assertEqual(requests[0]['appendDimension']['dimension'],'COLUMNS')
        self.assertTrue(any('COUNTA(' in str(x) for x in requests))
        self.assertTrue(any('*/P2·*' in str(x) for x in requests))
        series=[r['updateCells'] for r in requests if r.get('updateCells',{}).get('start',{}).get('columnIndex')==27]
        self.assertEqual([r['rows'][0]['values'][0]['userEnteredValue']['stringValue'] for r in series],['促销卡系列代码','P2',PENDING])
        directory[5:5]=[['P2','↳ DLPC / P2',1],[PENDING,'↳ DLPC / '+PENDING,1]]
        meta['sheets'][0]['properties']['gridProperties']['columnCount']=28
        repeated=build_requests(meta,rows,[],directory,[],promo)
        self.assertFalse(any('insertDimension' in r or 'appendDimension' in r for r in repeated))
        self.assertTrue(any('1 个系列/产品分类' in str(r) for r in repeated))
