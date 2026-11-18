import unittest
from directory_changes import change_requests, MARKER

class DirectoryChangesTests(unittest.TestCase):
    def test_reorder_insert_and_total_snapshot(self):
        before=[[],[],[],[],['P1','Promo',3,'old'],['1','First',10,'note'],['','合计',13,'total']]
        req=change_requests({'properties':{'sheetId':7}},before,['First','New','Promo','合计'],9)
        data=next(r['updateCells']['rows'] for r in req if 'updateCells' in r)
        def values(row):
            return [next(iter(c.get('userEnteredValue',{'blank':''}).values())) for c in row['values']]
        self.assertEqual(values(data[1]),[10,'1','note','已有'])
        self.assertEqual(values(data[2]),['','','','新增'])
        self.assertEqual(values(data[3]),[3,'P1','old','已有'])
        self.assertEqual(values(data[4]),[13,'','total','已有'])
        self.assertEqual(values(data[5]),['']*4)
        rules=[r['addConditionalFormatRule']['rule'] for r in req if 'addConditionalFormatRule' in r]
        self.assertEqual(len(rules),2)
        self.assertEqual(rules[0]['ranges'][0]['endRowIndex'],8)
        self.assertIn('$C5<>$E5',str(rules))
    def test_repeated_run_replaces_only_managed_rules(self):
        own={'booleanRule':{'condition':{'values':[{'userEnteredValue':MARKER}]}}}
        user={'booleanRule':{'condition':{'values':[{'userEnteredValue':'=C5>0'}]}}}
        before=[[],[],[],['','','','',MARKER],['1','First',10,'note',8,'1','note','已有']]
        req=change_requests({'properties':{'sheetId':7},'conditionalFormats':[own,user,own]},before,['First'],5)
        self.assertEqual([r['deleteConditionalFormatRule']['index'] for r in req if 'deleteConditionalFormatRule' in r],[2,0])
        snapshot=next(r['updateCells']['rows'][1] for r in req if 'updateCells' in r)
        self.assertEqual(snapshot['values'][0]['userEnteredValue']['numberValue'],10)
    def test_user_columns_are_not_overwritten(self):
        with self.assertRaises(RuntimeError):
            change_requests({'properties':{'sheetId':7}},[[],[],[],[],['1','First',10,'','user note']],['First'],5)
