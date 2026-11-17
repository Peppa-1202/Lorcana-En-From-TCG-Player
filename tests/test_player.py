import unittest
from unittest.mock import patch
from sync_lorcanaplayer import listing, detail_row, compare, plan_rows, memberships, directory_rows, HEADERS,collect_catalog

HTML='''<main><article class="type-card" id="post-10"><h5><a href="https://lorcanaplayer.com/card/example/">Example</a></h5></article></main><span data-wpc-total-found-posts="1"></span><div class="wpc-filter-card_set"><li class="wpc-term-item"><a class="wpc-filter-link">Promo Set 1</a><span class="wpc-term-count-value">1</span></li><li class="wpc-term-item"><a class="wpc-filter-link">Disney100</a><span class="wpc-term-count-value">1</span></li></div>'''
class PlayerTests(unittest.TestCase):
    def test_multiple_sets_can_exceed_catalogue_total(self):
        x=listing(HTML);self.assertEqual(x['total'],1);self.assertEqual(sum(x['sets'].values()),2)
    def test_blocked_or_empty_page_stops(self):
        with self.assertRaises(RuntimeError):listing('<h1>Verify access</h1>')
    def test_repeated_pages_stop(self):
        first=HTML.replace('data-wpc-total-found-posts="1"','data-wpc-total-found-posts="2"')+'<a class="page-numbers" href="https://lorcanaplayer.com/cards/page/2/">2</a>'
        with patch('sync_lorcanaplayer.fetch',return_value=first):
            with self.assertRaisesRegex(RuntimeError,'Repeated IDs'):collect_catalog()
    def test_identity_not_just_counts(self):
        row=['9','Promo Set 1']+['']*20+['https://lorcanaplayer.com/card/old/']
        ids,report=compare([HEADERS,row],{'cards':{'10':{'url':'https://lorcanaplayer.com/card/example/'}}})
        self.assertEqual(ids,['10']);self.assertEqual(report['sheet_only_ids'],['9'])
    def test_duplicate_or_wrong_url_stops(self):
        row=['10','Promo Set 1']+['']*20+['https://lorcanaplayer.com/card/old/']
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):compare([HEADERS,row,row],{'cards':{}})
        with self.assertRaisesRegex(RuntimeError,'mismatch'):compare([HEADERS,row],{'cards':{'10':{'url':'https://lorcanaplayer.com/card/example/'}}})
    def test_blank_fill_preserves_existing_positions(self):
        rows=[HEADERS,['1'],[],['2']];after,writes=plan_rows(rows,[['3'],['4']])
        self.assertEqual(after,[HEADERS,['1'],['3'],['2'],['4']]);self.assertEqual([i for i,r in writes],[2,4])
    def test_detail_symbols_and_optional_fields(self):
        fields={'Name':'Example','Card Type':'Action','Set':'<a>Promo Set 1</a><span> </span><a>Disney100</a>','Ink Cost':'0','Card ID':'1/P1','Card Text':'<strong>TEST</strong> 1 <img alt="Ink"/> – Draw.<br/>Then exert <img alt="Exert"/>.'}
        html='<main><article class="type-card" id="post-10"><h1>Example</h1><div class="card-details">'+''.join('<div><p class="card-label">'+k+'</p><p>'+v+'</p></div>' for k,v in fields.items())+'</div></article></main>'
        row,sets=detail_row(html,{'id':'10','url':'https://lorcanaplayer.com/card/example/'})
        self.assertEqual(row[8],0);self.assertIn('[Ink]',row[16]);self.assertIn('[Exert]',row[16]);self.assertEqual(row[13],'');self.assertEqual(sets,['Promo Set 1','Disney100'])
    def test_multi_series_unique_total(self):
        names={'Promo Set 1':1,'Disney100':1};row=['10','Promo Set 1 Disney100','1/P1']
        self.assertEqual(set(memberships(row[1],names)),set(names))
        result=directory_rows([HEADERS,row],{'sets':names,'total':1},{'missing_ids':[],'sheet_only_ids':[]})
        self.assertEqual(result[4][2]+result[5][2],2)
        self.assertEqual(next(r[2] for r in result if r[1]=='合计（页面 ID 去重）'),1)
