"""Native Sheets append, preserving existing content and directory formulas."""
from copy import deepcopy
from datetime import datetime
import json
import os
from zoneinfo import ZoneInfo

DETAIL = '洛卡纳TCG'
DIRECTORY = '洛卡纳TCG目录'

def connect():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw:
        raise RuntimeError('Set GOOGLE_SERVICE_ACCOUNT_JSON (GitHub Actions repository secret)')
    credentials = service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return build('sheets','v4',credentials=credentials,cache_discovery=False).spreadsheets()

def read_rows(service, sid):
    meta = service.get(spreadsheetId=sid,fields='sheets.properties').execute()
    sheet = next(s['properties'] for s in meta['sheets'] if s['properties']['title']==DETAIL)
    last = 'AB' if sheet['gridProperties']['columnCount'] >= 28 else 'AA' if sheet['gridProperties']['columnCount'] >= 27 else 'Z'
    return service.values().get(spreadsheetId=sid,range=f"'{DETAIL}'!A1:{last}{sheet['gridProperties']['rowCount']}",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])

def cell(value):
    if value is None or value == '':
        return {}
    return {'userEnteredValue': {'numberValue':value} if isinstance(value,(int,float)) else {'stringValue':str(value)}}

def update(sid,row,col,values,formula=False):
    cells = [{'userEnteredValue':{'formulaValue':v}} if formula else cell(v) for v in values]
    return {'updateCells':{'start':{'sheetId':sid,'rowIndex':row,'columnIndex':col},'rows':[{'values':cells}],'fields':'userEnteredValue'}}

def directory_requests(dsid, all_rows, directory, end, promo, sheet=None):
    from promo_ocr import PROMO, PENDING, subtype
    import re
    marker='↳ DLPC / '
    total_idx=next(i for i,r in enumerate(directory) if len(r)>1 and r[1]=='合计')
    parents=[(r+['']*4)[:4] for r in directory[4:total_idx] if len(r)>1 and not str(r[1]).startswith(marker)]
    known={r[1] for r in parents}
    for row in all_rows:
        if len(row)>1 and row[1] not in known:
            parents.append([row[2] if len(row)>2 else '',row[1],'','按网站 Cards 分类；各商品 ID 分别计数。'])
            known.add(row[1])
    groups=set()
    if promo is not None:
        for row in all_rows:
            if len(row)>1 and row[1]==PROMO:
                result=promo.get(str(row[0]),{})
                groups.add(subtype(result.get('code',row[26] if len(row)>26 else '')))
    def natural(value):
        return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)',value)]
    groups=sorted(groups-{PENDING},key=natural)+([PENDING] if PENDING in groups else [])
    output=[]
    for parent in parents:
        output.append(parent)
        if parent[1]==PROMO and promo is not None:
            parent[3]='按卡图左下角代码分组；下列子类已包含在 DLPC 总数内，不重复计入总计。'
            output.extend([[g,marker+g,'','卡图无法可靠识别，保留待核查。' if g==PENDING else '按商品 ID 计数；同代码不同版本分别计数。'] for g in groups])
    new_total=4+len(output); delta=new_total-total_idx; req=[]
    if delta>0:
        req.append({'insertDimension':{'range':{'sheetId':dsid,'dimension':'ROWS','startIndex':total_idx,'endIndex':new_total},'inheritFromBefore':True}})
    elif delta<0:
        req.append({'deleteDimension':{'range':{'sheetId':dsid,'dimension':'ROWS','startIndex':new_total,'endIndex':total_idx}}})
    for i,row in enumerate(output,4):
        req.append(update(dsid,i,0,row))
        if row[1].startswith(marker):
            group=row[0]
            criterion=PENDING if group==PENDING else '*/'+group+'·*'
            formula=f'=COUNTIFS(\'{DETAIL}\'!$B$2:$B${end},"{PROMO}",\'{DETAIL}\'!$AA$2:$AA${end},"{criterion}")'
            if group=='无编号':
                formula=f'=SUMPRODUCT(N(\'{DETAIL}\'!$B$2:$B${end}="{PROMO}"),N(REGEXMATCH(\'{DETAIL}\'!$AA$2:$AA${end},"^(EN|FR|DE|IT|ES|JA|ZH)·[0-9]+$")))'
            bg={'red':.94,'green':.97,'blue':1}
        else:
            formula=f"=COUNTIF('{DETAIL}'!$B$2:$B${end},B{i+1})"
            bg={'red':1,'green':1,'blue':1}
        req.append(update(dsid,i,2,[formula],True))
        req.append({'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':i,'endRowIndex':i+1,'startColumnIndex':0,'endColumnIndex':4},'cell':{'userEnteredFormat':{'backgroundColor':bg,'textFormat':{'fontFamily':'Arial','fontSize':10,'bold':False},'wrapStrategy':'WRAP','verticalAlignment':'MIDDLE'}},'fields':'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment'}})
    req.append(update(dsid,new_total,2,[f"=COUNTA('{DETAIL}'!$A$2:$A${end})"],True))
    req.append(update(dsid,new_total,3,[f'{len(parents)} 个系列/产品分类；每个商品 ID 仅计一次，DLPC 子类不重复计入。']))
    date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    check_idx=next((i for i,r in enumerate(directory) if r and r[0]=='最近完整性核查'),len(directory))
    req.append(update(dsid,check_idx+delta,0,['最近完整性核查',date]))
    from directory_changes import change_requests
    req.extend(change_requests(sheet or {"properties": {"sheetId": dsid}}, directory,
                               [r[1] for r in output]+["合计"], len(directory)+delta))
    return req

def build_requests(meta,rows,missing,directory,exemplar,promo=None):
    sheets = {s['properties']['title']:s for s in meta['sheets']}
    detail = sheets[DETAIL]; prop=detail['properties']; did=prop['sheetId']
    dsid=sheets[DIRECTORY]['properties']['sheetId']
    n=len(rows); end=n+len(missing); requests=[]
    if end > prop['gridProperties']['rowCount']:
        requests.append({'appendDimension':{'sheetId':did,'dimension':'ROWS','length':end-prop['gridProperties']['rowCount']}})
    new_cells=[]
    for row in missing:
        cells=[]
        for col,value in enumerate(row):
            source=exemplar[col] if col<len(exemplar) else {}
            if source.get('dataValidation') or source.get('chipRuns') or 'formulaValue' in source.get('userEnteredValue',{}):
                raise RuntimeError('Template row has new formula/validation/chip structure; review mapping')
            c=cell(value)
            fmt=deepcopy(source.get('userEnteredFormat',{}))
            if 'textFormat' in fmt:
                fmt['textFormat'].pop('link',None)
            if col in (22,23) and value:
                fmt.setdefault('textFormat',{})['link']={'uri':str(value)}
            c['userEnteredFormat']=fmt
            cells.append(c)
        new_cells.append({'values':cells})
    if new_cells:
        requests.append({'updateCells':{'start':{'sheetId':did,'rowIndex':n,'columnIndex':0},'rows':new_cells,'fields':'userEnteredValue,userEnteredFormat'}})
    requests.extend(directory_requests(dsid, rows[1:]+missing, directory, end, promo, sheets[DIRECTORY]))
    if promo is not None:
        if prop['gridProperties'].get('columnCount',26)<28:
            requests.insert(0,{'appendDimension':{'sheetId':did,'dimension':'COLUMNS','length':28-prop['gridProperties'].get('columnCount',26)}})
        requests.append(update(did,0,26,['卡牌代码']))
        requests.append({'repeatCell':{'range':{'sheetId':did,'startColumnIndex':26,'endColumnIndex':27},'cell':{'userEnteredFormat':{'numberFormat':{'type':'TEXT'},'textFormat':{'fontFamily':'Arial','fontSize':10},'verticalAlignment':'MIDDLE','wrapStrategy':'CLIP'}},'fields':'userEnteredFormat'}})
        requests.append({'copyPaste':{'source':{'sheetId':did,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':25,'endColumnIndex':26},'destination':{'sheetId':did,'startRowIndex':0,'endRowIndex':1,'startColumnIndex':26,'endColumnIndex':27},'pasteType':'PASTE_FORMAT'}})
        requests.append({'updateDimensionProperties':{'range':{'sheetId':did,'dimension':'COLUMNS','startIndex':26,'endIndex':27},'properties':{'pixelSize':220},'fields':'pixelSize'}})
        from promo_ocr import subtype
        requests.append(update(did,0,27,['促销卡系列代码']))
        requests.append({'copyPaste':{'source':{'sheetId':did,'startColumnIndex':26,'endColumnIndex':27,'startRowIndex':0,'endRowIndex':end},'destination':{'sheetId':did,'startColumnIndex':27,'endColumnIndex':28,'startRowIndex':0,'endRowIndex':end},'pasteType':'PASTE_FORMAT'}})
        requests.append({'updateDimensionProperties':{'range':{'sheetId':did,'dimension':'COLUMNS','startIndex':27,'endIndex':28},'properties':{'pixelSize':150},'fields':'pixelSize'}})
        for i,row in enumerate(rows[1:]+missing,1):
            result=promo.get(str(row[0])) if row else None
            if result:
                c=cell(result['code']); c['note']='卡图左下角代码；来源：'+result['source']+('；'+result['error'] if result.get('error') else '')
                requests.append({'updateCells':{'start':{'sheetId':did,'rowIndex':i,'columnIndex':26},'rows':[{'values':[c]}],'fields':'userEnteredValue,note'}})
                requests.append(update(did,i,27,[subtype(result['code'])]))
    if missing:
        for table in detail.get('tables',[]):
            rng=table['range']
            if rng.get('startColumnIndex',0)==0 and rng.get('endColumnIndex')==26 and rng.get('endRowIndex')==n:
                requests.append({'updateTable':{'table':{'tableId':table['tableId'],'range':{**rng,'endRowIndex':end}},'fields':'range'}})
        basic=detail.get('basicFilter')
        if basic and basic['range'].get('endRowIndex')==n:
            basic=deepcopy(basic);basic['range']['endRowIndex']=end
            if promo is not None and basic['range'].get('endColumnIndex') in (26,27):
                basic['range']['endColumnIndex']=28
            if basic.get('filterSpecs'):
                basic.pop('criteria',None)
            requests.append({'setBasicFilter':{'filter':basic}})
    return requests

def apply_missing(service,sid,rows,missing,promo=None):
    if read_rows(service,sid)!=rows:
        raise RuntimeError('Sheet changed during audit; rerun')
    meta=service.get(spreadsheetId=sid,includeGridData=False).execute()
    directory=service.values().get(spreadsheetId=sid,range=f"'{DIRECTORY}'!A1:H200",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])
    data=service.get(spreadsheetId=sid,ranges=[f"'{DETAIL}'!A{len(rows)}:Z{len(rows)}"],includeGridData=True).execute()
    exemplar=data['sheets'][0]['data'][0]['rowData'][0]['values']
    requests=build_requests(meta,rows,missing,directory,exemplar,promo)
    # One atomic batch: never auto-retry an uncertain write. A rerun re-reads IDs.
    service.batchUpdate(spreadsheetId=sid,body={'requests':requests}).execute(num_retries=0)
    current=read_rows(service,sid)
    expected=[str(r[0]) for r in rows[1:]+missing if r and r[0]]
    actual=[str(r[0]) for r in current[1:] if r and r[0]]
    if actual != expected:
        raise RuntimeError('Write verification failed; inspect Sheet before retry')
    if promo is not None:
        from promo_ocr import subtype
        for row in current[1:]:
            expected_code=promo.get(str(row[0])) if row else None
            if expected_code and (len(row)<28 or row[26]!=expected_code['code'] or row[27]!=subtype(expected_code['code'])):
                raise RuntimeError('Promo code write verification failed')
    for want,got in zip(missing,current[len(rows):]):
        got=got[:26]
        if [str(x) if x is not None else '' for x in want] != [str(x) if x is not None else '' for x in got]+['']*(26-len(got)):
            # Numbers can round-trip as 1 instead of 1.0; compare numerically too.
            if want != got+['']*(26-len(got)):
                raise RuntimeError('New row verification failed')
