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
    return service.values().get(spreadsheetId=sid,range=f"'{DETAIL}'!A1:Z{sheet['gridProperties']['rowCount']}",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])

def cell(value):
    if value is None or value == '':
        return {}
    return {'userEnteredValue': {'numberValue':value} if isinstance(value,(int,float)) else {'stringValue':str(value)}}

def update(sid,row,col,values,formula=False):
    cells = [{'userEnteredValue':{'formulaValue':v}} if formula else cell(v) for v in values]
    return {'updateCells':{'start':{'sheetId':sid,'rowIndex':row,'columnIndex':col},'rows':[{'values':cells}],'fields':'userEnteredValue'}}

def build_requests(meta,rows,missing,directory,exemplar):
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
    total_idx=next(i for i,r in enumerate(directory) if len(r)>1 and r[1]=='合计')
    known={r[1] for r in directory[4:total_idx] if len(r)>1}
    all_rows=rows[1:]+missing
    new_sets=list(dict.fromkeys(r[1] for r in all_rows if len(r)>1 and r[1] not in known))
    if new_sets:
        requests.append({'insertDimension':{'range':{'sheetId':dsid,'dimension':'ROWS','startIndex':total_idx,'endIndex':total_idx+len(new_sets)},'inheritFromBefore':True}})
        for i,name in enumerate(new_sets):
            code=next((r[2] for r in all_rows if len(r)>2 and r[1]==name),'')
            requests.append(update(dsid,total_idx+i,0,[code,name,'','按网站 Cards 分类；各商品 ID 分别计数。']))
    new_total=total_idx+len(new_sets)
    for i in range(4,new_total):
        requests.append(update(dsid,i,2,[f"=COUNTIF('{DETAIL}'!$B$2:$B${end},B{i+1})"],True))
    requests.append(update(dsid,new_total,2,[f'=SUM(C5:C{new_total})'],True))
    requests.append(update(dsid,new_total,3,[f'{new_total-4} 个系列/产品分类；每个商品 ID 仅计一次。']))
    date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    check_idx=next((i for i,r in enumerate(directory) if r and r[0]=='最近完整性核查'),len(directory))
    requests.append(update(dsid,check_idx+len(new_sets),0,['最近完整性核查',date]))
    if missing:
        for table in detail.get('tables',[]):
            rng=table['range']
            if rng.get('startColumnIndex',0)==0 and rng.get('endColumnIndex')==26 and rng.get('endRowIndex')==n:
                requests.append({'updateTable':{'table':{'tableId':table['tableId'],'range':{**rng,'endRowIndex':end}},'fields':'range'}})
        basic=detail.get('basicFilter')
        if basic and basic['range'].get('endRowIndex')==n:
            basic=deepcopy(basic);basic['range']['endRowIndex']=end
            if basic.get('filterSpecs'):
                basic.pop('criteria',None)
            requests.append({'setBasicFilter':{'filter':basic}})
    return requests

def apply_missing(service,sid,rows,missing):
    if read_rows(service,sid)!=rows:
        raise RuntimeError('Sheet changed during audit; rerun')
    meta=service.get(spreadsheetId=sid,includeGridData=False).execute()
    directory=service.values().get(spreadsheetId=sid,range=f"'{DIRECTORY}'!A1:D200",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])
    data=service.get(spreadsheetId=sid,ranges=[f"'{DETAIL}'!A{len(rows)}:Z{len(rows)}"],includeGridData=True).execute()
    exemplar=data['sheets'][0]['data'][0]['rowData'][0]['values']
    requests=build_requests(meta,rows,missing,directory,exemplar)
    # One atomic batch: never auto-retry an uncertain write. A rerun re-reads IDs.
    service.batchUpdate(spreadsheetId=sid,body={'requests':requests}).execute(num_retries=0)
    current=read_rows(service,sid)
    expected=[str(r[0]) for r in rows[1:]+missing if r and r[0]]
    actual=[str(r[0]) for r in current[1:] if r and r[0]]
    if actual != expected:
        raise RuntimeError('Write verification failed; inspect Sheet before retry')
    for want,got in zip(missing,current[len(rows):]):
        if [str(x) if x is not None else '' for x in want] != [str(x) if x is not None else '' for x in got]+['']*(26-len(got)):
            # Numbers can round-trip as 1 instead of 1.0; compare numerically too.
            if want != got+['']*(26-len(got)):
                raise RuntimeError('New row verification failed')
