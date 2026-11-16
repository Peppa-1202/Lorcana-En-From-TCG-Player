import unittest
from unittest.mock import patch
from sync_lorcana import compare, to_row, collect_catalog, HEADERS
from sheets_sync import build_requests

P={'productId':1,'productName':'Example','productLineId':71,'sealed':False,'setName':'Promo','setCode':'upstream','customAttributes':{'description':'<b>Ability</b><br>Draw &amp; play.','costInk':'0'},'marketPrice':0}

class SyncTests(unittest.TestCase):
    def test_mapping_and_zero(self):
        r=to_row(P,{'Promo':'DLPC'})
        self.assertEqual(len(r),26)
        self.assertEqual(r[2],'DLPC')
        self.assertEqual(r[7],'0')
        self.assertEqual(r[15],'Ability\nDraw & play.')
        self.assertEqual(r[18],0)
    def test_repeat_is_idempotent(self):
        rows=[HEADERS]
        missing,report=compare(rows,{'1':P})
        self.assertEqual(report['missing_ids'],['1'])
        self.assertEqual(compare(rows+missing,{'1':P})[0],[])
    def test_duplicate_ids_stop(self):
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):
            compare([HEADERS,['1'],['1']],{'1':P})
    def test_sheet_only_never_deleted(self):
        missing,report=compare([HEADERS,['2','Promo','DLPC']],{'1':P})
        self.assertEqual(report['sheet_only_ids'],['2'])
        self.assertEqual(missing[0][2],'DLPC')
    def test_repeated_page_stops(self):
        first={'totalResults':2,'aggregations':{'setName':[{'urlValue':'promo','value':'Promo','count':2}]},'results':[]}
        page={'totalResults':2,'results':[P]}
        with patch('sync_lorcana.search',side_effect=[first,page,page]):
            with self.assertRaisesRegex(RuntimeError,'Repeated'):
                collect_catalog()
    def test_totals_stop_partial_snapshot(self):
        bad={'totalResults':2,'aggregations':{'setName':[{'count':1}]},'results':[]}
        with patch('sync_lorcana.search',return_value=bad):
            with self.assertRaisesRegex(RuntimeError,'totals disagree'):
                collect_catalog()
    def test_atomic_append_new_set_and_link(self):
        meta={'sheets':[{'properties':{'title':'洛卡纳TCG','sheetId':1,'gridProperties':{'rowCount':2}}},{'properties':{'title':'洛卡纳TCG目录','sheetId':2}}]}
        rows=[HEADERS,['2','Old','2']]
        new=[to_row(P,{})]
        directory=[['Title'],[],[],['code','set','count'],['2','Old',1],['','合计',1],[],['采集日期','old']]
        exemplar=[{'userEnteredFormat':{'textFormat':{'link':{'uri':'old'}}}} for _ in range(26)]
        req=build_requests(meta,rows,new,directory,exemplar)
        self.assertEqual(req[0]['appendDimension']['length'],1)
        written=req[1]['updateCells']['rows'][0]['values']
        self.assertEqual(written[22]['userEnteredFormat']['textFormat']['link']['uri'],'https://www.tcgplayer.com/product/1')
        self.assertNotIn('link',written[0]['userEnteredFormat']['textFormat'])
        self.assertTrue(any('insertDimension' in x for x in req))
        self.assertTrue(any("$B$3" in str(x) for x in req))

if __name__=='__main__': unittest.main()
