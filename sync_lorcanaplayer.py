"""Audit LorcanaPlayer /cards/ by page ID and append missing details to Sheets."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

BASE='https://lorcanaplayer.com'
DETAIL='洛卡纳Player'
DIRECTORY='洛卡纳Player目录'
HEADERS=['LorcanaPlayer 页面 ID','系列名称','卡牌编号','英文完整卡名','角色 / 卡名','版本名称','稀有度','墨水颜色','墨水费用','可放墨水区','卡牌类型','角色分类','力量','意志力','传说值','移动费用','效果文本','背景文本','关键词 / 能力','画师','所属作品','发售日期（网站原文）','卡牌详情链接','卡图链接','采集日期','备注']

def fetch(url):
    if urlparse(url).hostname!='lorcanaplayer.com':
        raise RuntimeError('Unexpected source host')
    for attempt in range(3):
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html'})
            with urllib.request.urlopen(request,timeout=45) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            # Record only response diagnostics, never request credentials or cookies.
            try:
                body = exc.read(65536).decode('utf-8', errors='replace')
                page = BeautifulSoup(body, 'html.parser')
                title = page.title.get_text(' ', strip=True) if page.title else ''
                for node in page.select('script, style'):
                    node.decompose()
                headers = {
                    name: exc.headers.get(name)
                    for name in ('Content-Type', 'Server', 'CF-Ray', 'CF-Mitigated', 'Retry-After')
                    if exc.headers and exc.headers.get(name)
                }
                diagnostic = {
                    'url': url,
                    'status': exc.code,
                    'headers': headers,
                    'title': title,
                    'body_excerpt': page.get_text(' ', strip=True)[:2000],
                }
                print('HTTP response diagnostic: ' + json.dumps(diagnostic, ensure_ascii=True), flush=True)
            except Exception as diagnostic_error:
                print('Unable to read HTTP response diagnostic: ' + type(diagnostic_error).__name__, flush=True)
            if exc.code not in (429,500,502,503,504) or attempt==2:
                raise
            time.sleep(2**(attempt+1))
        except (OSError,TimeoutError):
            if attempt==2: raise
            time.sleep(2**(attempt+1))

def listing(html):
    soup=BeautifulSoup(html,'html.parser')
    total_node=soup.select_one('[data-wpc-total-found-posts]')
    if not total_node:
        raise RuntimeError('Missing catalogue total; refusing partial or challenge page')
    total=int(total_node['data-wpc-total-found-posts'])
    pages=max([1]+[int(m.group(1)) for a in soup.select('a.page-numbers[href]') if (m:=re.search(r'/cards/page/(\d+)/',a['href']))])
    cards={}
    for article in soup.select('main article.type-card'):
        pid=re.fullmatch(r'post-(\d+)',article.get('id',''))
        anchor=article.select_one('h5 a[href]')
        if not pid or not anchor or urlparse(anchor['href']).hostname!='lorcanaplayer.com' or not urlparse(anchor['href']).path.startswith('/card/'):
            raise RuntimeError('Invalid card listing structure')
        key=pid.group(1)
        if key in cards: raise RuntimeError('Repeated ID within catalogue page')
        cards[key]={'id':key,'url':anchor['href'],'name':anchor.get_text(' ',strip=True)}
    sets={}
    for item in soup.select('.wpc-filter-card_set li.wpc-term-item'):
        anchor=item.select_one('a.wpc-filter-link');count=item.select_one('.wpc-term-count-value')
        if anchor and count: sets[anchor.get_text(' ',strip=True)]=int(count.get_text())
    if not cards or not sets or total<=0:
        raise RuntimeError('Catalogue/set totals missing or inconsistent')
    return {'total':total,'pages':pages,'cards':cards,'sets':sets}

def collect_catalog():
    first=listing(fetch(BASE+'/cards/'))
    merged=dict(first['cards'])
    def page(number):
        result=listing(fetch(f'{BASE}/cards/page/{number}/'))
        if result['total']!=first['total'] or result['sets']!=first['sets']:
            raise RuntimeError('Catalogue changed during scan')
        print(f"LorcanaPlayer page {number}/{first['pages']}: {len(result['cards'])}",flush=True)
        return result['cards']
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(page,range(2,first['pages']+1)):
            if merged.keys() & result.keys(): raise RuntimeError('Repeated IDs across catalogue pages')
            merged.update(result)
    final=listing(fetch(BASE+'/cards/'))
    if len(merged)!=first['total'] or final['total']!=first['total'] or final['sets']!=first['sets'] or final['cards']!=first['cards']:
        raise RuntimeError('Incomplete or changing catalogue; no write')
    if len({v['url'] for v in merged.values()})!=len(merged):
        raise RuntimeError('Duplicate card URLs')
    return {'cards':merged,'sets':first['sets'],'total':first['total'],'pages':first['pages']}

def clean_node(node):
    node=deepcopy(node)
    for img in node.select('img'):
        alt=img.get('alt') or img.get('title')
        if not alt: raise RuntimeError('Unlabelled inline symbol; review source')
        img.replace_with('['+alt+']')
    for br in node.select('br'): br.replace_with('\n')
    return re.sub(r'[ \t\r\f\v]+',' ',node.get_text()).strip()

def detail_row(html, expected):
    soup=BeautifulSoup(html,'html.parser')
    article=soup.select_one('main article.type-card')
    if not article or article.get('id')!='post-'+expected['id']:
        raise RuntimeError('Card detail ID mismatch')
    heading=article.select_one('h1')
    if not heading: raise RuntimeError('Missing card heading')
    fields={}
    sets=[]
    for label in article.select('.card-details .card-label'):
        name=label.get_text(' ',strip=True)
        siblings=list(label.find_next_siblings())
        fields[name]='\n\n'.join(clean_node(n) for n in siblings).strip()
        if name=='Set':
            sets=[a.get_text(' ',strip=True) for n in siblings for a in n.select('a')]
    if not fields.get('Name') or not fields.get('Set') or not fields.get('Card Type'):
        raise RuntimeError('Missing required card detail fields')
    image=article.select_one('img.card')
    image_url=image.parent.get('href') if image and image.parent.name=='a' else image.get('src','') if image else ''
    def number(key):
        value=fields.get(key,'')
        return int(value) if re.fullmatch(r'\d+',value) else value
    row=[expected['id'],fields['Set'],fields.get('Card ID',''),heading.get_text(' ',strip=True),fields['Name'],fields.get('Version',''),fields.get('Rarity',''),fields.get('Ink Color',''),number('Ink Cost'),fields.get('Inkwell',''),fields['Card Type'],fields.get('Classifications',''),number('Strength'),number('Willpower'),number('Lore'),number('Move Cost'),fields.get('Card Text',''),fields.get('Flavor Text',''),fields.get('Keywords + Abilities',''),fields.get('Illustrator',''),fields.get('Franchise',''),fields.get('Release Date',''),expected['url'],image_url,datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),'LorcanaPlayer 完整性核查补录']
    return row,sets

def compare(rows,catalog):
    if not rows or rows[0]!=HEADERS: raise RuntimeError('LorcanaPlayer headers changed')
    existing={}
    for index,row in enumerate(rows[1:],2):
        if not any(v not in ('',None) for v in row): continue
        if not row or not str(row[0]).isdigit(): raise RuntimeError(f'Invalid page ID at row {index}')
        pid=str(row[0])
        if pid in existing: raise RuntimeError('Duplicate page ID: '+pid)
        if len(row)<23: raise RuntimeError('Incomplete existing row: '+str(index))
        existing[pid]=row
    cards=catalog['cards']
    mismatches=[pid for pid in existing.keys()&cards.keys() if existing[pid][22].rstrip('/')!=cards[pid]['url'].rstrip('/')]
    if mismatches: raise RuntimeError('Existing ID/URL mismatch: '+','.join(mismatches))
    missing=sorted(cards.keys()-existing.keys(),key=int,reverse=True)
    return missing,{'website_count':len(cards),'sheet_count':len(existing),'missing_ids':missing,'sheet_only_ids':sorted(existing.keys()-cards.keys()),'duplicate_ids':[],'blank_rows':len(rows)-1-len(existing)}

def fetch_missing(catalog,ids,cache_dir):
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True)
    def one(pid):
        path=cache/(pid+'.json')
        if path.exists():
            result=json.loads(path.read_text())
            if result['row'][22]!=catalog['cards'][pid]['url']: raise RuntimeError('Cached URL changed')
            return result
        html=fetch(catalog['cards'][pid]['url'])
        row,sets=detail_row(html,catalog['cards'][pid])
        result={'row':row,'sets':sets}
        path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        return result
    results=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(one,ids),1):
            results.append(r)
            if i%25==0 or i==len(ids):print(f'Card details {i}/{len(ids)}',flush=True)
    return results

SET_CODES={name:str(i) for i,name in enumerate(['The First Chapter','Rise of the Floodborn','Into the Inklands',"Ursula's Return",'Shimmering Skies','Azurite Sea',"Archazia's Island",'Reign of Jafar','Fabled','Whispers in the Well','Winterspell','Wilds Unknown','Attack of the Vine!','Hyperia City','Into the Inkdark'],1)}
SET_CODES.update({"Illumineer's Quest: Deep Trouble":'Q1',"Illumineer's Quest: Palace Heist":'Q2',"Illumineer's Quest: The Great Hunny Rescue":'Q3','Disney100':'D100','D23 Collection':'D23'})

def memberships(value,names):
    remaining=str(value or '').strip();found=[]
    for name in sorted(set(names)|{'D23 Expo'},key=len,reverse=True):
        pattern=r'(?<!\S)'+re.escape(name)+r'(?!\S)'
        if re.search(pattern,remaining):
            found.append(name);remaining=re.sub(pattern,' ',remaining).strip()
    if remaining: found.append(re.sub(r'\s+',' ',remaining))
    return found

def directory_rows(rows,catalog,report):
    grouped={name:[] for name in catalog['sets']}
    for row in rows[1:]:
        if not row or not row[0]:continue
        for name in memberships(row[1],catalog['sets']):grouped.setdefault(name,[]).append(row)
    date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    output=[['LorcanaPlayer 卡牌目录','','',''],['按网站系列归属统计；同一卡牌可属于多个系列。','','',''],['','','',''],['系列代码','系列 / 产品名称','LorcanaPlayer 记录数','备注']]
    for name,records in grouped.items():
        suffixes={m.group(1) for r in records if len(r)>2 and (m:=re.search(r'/([A-Z]+\d*)$',str(r[2] or '')))}
        code=SET_CODES.get(name) or (next(iter(suffixes)) if len(suffixes)==1 else '')
        notes=[]
        expected=catalog['sets'].get(name)
        if expected is None:notes.append('明细存在此归属；网站筛选器未单列。')
        elif len(records)!=expected:notes.append(f'网站筛选器 {expected} 条；明细归属 {len(records)} 条，需复核分类。')
        else:notes.append('与网站系列计数一致。')
        multi=sum(len(memberships(r[1],catalog['sets']))>1 for r in records)
        if multi:notes.append(f'含 {multi} 条同时归属其他系列的卡牌。')
        if not code:notes.append('来源未确认系列代码，留空。')
        output.append([code,name,len(records),' '.join(notes)])
    output.extend([['','合计（页面 ID 去重）',sum(bool(r and r[0]) for r in rows[1:]),'同一卡牌多系列归属不重复计入此合计；系列行不可直接相加作为卡牌总数。'],['','','',''],['网站当前卡牌数',catalog['total'],'',''],['最近完整性核查',date,'',''],['本次补录数',len(report['missing_ids']),'',''],['仅表格存在数',len(report['sheet_only_ids']),'','不自动删除。'],['数据来源',BASE+'/cards/','','']])
    return output

def plan_rows(rows,missing):
    # Fill only completely empty rows; preserve every populated row and its position.
    empty=[i for i,r in enumerate(rows) if i>0 and not any(v not in ('',None) for v in r)]
    result=deepcopy(rows);writes=[]
    for row in missing:
        index=empty.pop(0) if empty else len(result)
        if index==len(result):result.append(row)
        else:result[index]=row
        writes.append((index,row))
    return result,writes

def make_requests(meta,rows,missing,catalog,report,exemplar,directory_before):
    from sheets_sync import cell
    props={s['properties']['title']:s for s in meta['sheets']}
    detail=props[DETAIL];did=detail['properties']['sheetId'];dprop=props[DIRECTORY]['properties'];dsid=dprop['sheetId']
    after,writes=plan_rows(rows,missing);directory=directory_rows(after,catalog,report);requests=[]
    for prop,count in [(detail['properties'],len(after)),(dprop,len(directory))]:
        if count>prop['gridProperties']['rowCount']:
            requests.append({'appendDimension':{'sheetId':prop['sheetId'],'dimension':'ROWS','length':count-prop['gridProperties']['rowCount']}})
    for source in exemplar:
        if source.get('dataValidation') or source.get('chipRuns') or 'formulaValue' in source.get('userEnteredValue',{}):
            raise RuntimeError('Structured template changed; review before writing')
    groups=[]
    for index,row in writes:
        if not groups or index!=groups[-1][-1][0]+1:groups.append([])
        groups[-1].append((index,row))
    exemplar_index=max(i for i,r in enumerate(rows) if r and r[0])
    for group in groups:
        requests.append({'copyPaste':{'source':{'sheetId':did,'startRowIndex':exemplar_index,'endRowIndex':exemplar_index+1,'startColumnIndex':0,'endColumnIndex':26},'destination':{'sheetId':did,'startRowIndex':group[0][0],'endRowIndex':group[-1][0]+1,'startColumnIndex':0,'endColumnIndex':26},'pasteType':'PASTE_FORMAT'}})
        new_cells=[]
        for index,row in group:
            cells=[]
            for col,value in enumerate(row):
                c=cell(value)
                if col in (22,23) and value:c['userEnteredFormat']={'textFormat':{'link':{'uri':value}}}
                cells.append(c)
            new_cells.append({'values':cells})
        requests.append({'updateCells':{'start':{'sheetId':did,'rowIndex':group[0][0],'columnIndex':0},'rows':new_cells,'fields':'userEnteredValue,userEnteredFormat.textFormat.link'}})
    height=max(len(directory),len(directory_before))
    data=directory+[['','','','']]*(height-len(directory))
    requests.append({'updateCells':{'range':{'sheetId':dsid,'startRowIndex':0,'endRowIndex':height,'startColumnIndex':0,'endColumnIndex':4},'rows':[{'values':[cell(v) for v in r]} for r in data],'fields':'userEnteredValue'}})
    requests.append({'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':0,'endRowIndex':height,'startColumnIndex':0,'endColumnIndex':4},'cell':{'userEnteredFormat':{'textFormat':{'fontFamily':'Arial','fontSize':10},'backgroundColor':{'red':1,'green':1,'blue':1},'wrapStrategy':'WRAP','verticalAlignment':'MIDDLE'}},'fields':'userEnteredFormat'}})
    for row in (3,len(directory)-7):
        requests.append({'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':row,'endRowIndex':row+1,'startColumnIndex':0,'endColumnIndex':4},'cell':{'userEnteredFormat':{'backgroundColor':{'red':.9,'green':.9,'blue':.9},'textFormat':{'bold':True}}},'fields':'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold'}})
    for top in (0,1):
        merged={'sheetId':dsid,'startRowIndex':top,'endRowIndex':top+1,'startColumnIndex':0,'endColumnIndex':4}
        if merged not in props[DIRECTORY].get('merges',[]):
            requests.append({'mergeCells':{'range':merged,'mergeType':'MERGE_ALL'}})
    requests.append({'repeatCell':{'range':{'sheetId':dsid,'startRowIndex':4,'endRowIndex':len(directory)-6,'startColumnIndex':2,'endColumnIndex':3},'cell':{'userEnteredFormat':{'numberFormat':{'type':'NUMBER','pattern':'#,##0'}}},'fields':'userEnteredFormat.numberFormat'}})
    requests.append({'setBasicFilter':{'filter':{'range':{'sheetId':dsid,'startRowIndex':3,'endRowIndex':len(directory)-7,'startColumnIndex':0,'endColumnIndex':4}}}})
    for index,width in enumerate((120,420,180,560)):
        requests.append({'updateDimensionProperties':{'range':{'sheetId':dsid,'dimension':'COLUMNS','startIndex':index,'endIndex':index+1},'properties':{'pixelSize':width},'fields':'pixelSize'}})
    requests.append({'autoResizeDimensions':{'dimensions':{'sheetId':dsid,'dimension':'ROWS','startIndex':0,'endIndex':len(directory)}}})
    requests.append({'updateSheetProperties':{'properties':{'sheetId':dsid,'gridProperties':{'frozenRowCount':4}},'fields':'gridProperties.frozenRowCount'}})
    for table in detail.get('tables',[]):
        rng=table['range']
        if rng.get('endColumnIndex')==26 and rng.get('endRowIndex')==len(rows):requests.append({'updateTable':{'table':{'tableId':table['tableId'],'range':{**rng,'endRowIndex':len(after)}},'fields':'range'}})
    basic=detail.get('basicFilter')
    if basic and basic['range'].get('endRowIndex')==len(rows):
        basic=deepcopy(basic);basic['range']['endRowIndex']=len(after)
        if basic.get('filterSpecs'):basic.pop('criteria',None)
        requests.append({'setBasicFilter':{'filter':basic}})
    from directory_changes import change_requests
    requests.extend(change_requests(props[DIRECTORY], directory_before,
                                   [r[1] for r in directory[4:len(directory)-6]], height))
    return requests,after,directory

def read_rows(service,sid):
    meta=service.get(spreadsheetId=sid,fields='sheets.properties').execute()
    prop=next(s['properties'] for s in meta['sheets'] if s['properties']['title']==DETAIL)
    return service.values().get(spreadsheetId=sid,range=f"'{DETAIL}'!A1:Z{prop['gridProperties']['rowCount']}",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])

def apply(service,sid,rows,missing,catalog,report):
    if read_rows(service,sid)!=rows:raise RuntimeError('Sheet changed during audit; rerun')
    meta=service.get(spreadsheetId=sid,includeGridData=False).execute()
    directory=service.values().get(spreadsheetId=sid,range=f"'{DIRECTORY}'!A1:H200",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])
    if directory and directory[0] and directory[0][0]!='LorcanaPlayer 卡牌目录':raise RuntimeError('Directory contains unexpected data; refusing overwrite')
    last=max(i for i,r in enumerate(rows) if r and r[0])+1
    source=service.get(spreadsheetId=sid,ranges=[f"'{DETAIL}'!A{last}:Z{last}"],includeGridData=True).execute()
    exemplar=source['sheets'][0]['data'][0]['rowData'][0]['values']
    requests,expected,expected_directory=make_requests(meta,rows,missing,catalog,report,exemplar,directory)
    service.batchUpdate(spreadsheetId=sid,body={'requests':requests}).execute(num_retries=0)
    actual=read_rows(service,sid)
    normalize=lambda rr:[[('' if v is None else v) for v in r]+['']*(26-len(r)) for r in rr]
    if normalize(actual)!=normalize(expected):raise RuntimeError('Detail write verification failed; inspect before retry')
    got=service.values().get(spreadsheetId=sid,range=f"'{DIRECTORY}'!A1:D{len(expected_directory)}",valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])
    if normalize(got)!=normalize(expected_directory):raise RuntimeError('Directory write verification failed')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--spreadsheet-id',default=os.getenv('SPREADSHEET_ID',''))
    parser.add_argument('--existing-json',help='Offline audit only')
    parser.add_argument('--catalog-json',help='Offline audit only')
    parser.add_argument('--output',default='output/player')
    args=parser.parse_args()
    if args.apply and (args.existing_json or args.catalog_json):parser.error('--apply requires live reads')
    if not args.existing_json and not args.spreadsheet_id:parser.error('Set SPREADSHEET_ID')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    from sheets_sync import connect
    service=None if args.existing_json else connect()
    rows=json.loads(Path(args.existing_json).read_text()) if args.existing_json else read_rows(service,args.spreadsheet_id)
    catalog=json.loads(Path(args.catalog_json).read_text()) if args.catalog_json else collect_catalog()
    (out/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
    ids,report=compare(rows,catalog)
    results=fetch_missing(catalog,ids,out/'details')
    missing=[r['row'] for r in results]
    (out/'missing_rows.json').write_text(json.dumps(missing,ensure_ascii=False),encoding='utf-8')
    report.update({'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'applied':False})
    if args.apply:
        # Catch changes that occurred during a long first-time backfill.
        final=listing(fetch(BASE+'/cards/'))
        if final['total']!=catalog['total'] or final['sets']!=catalog['sets']:raise RuntimeError('Catalogue changed during detail fetch; rerun')
        apply(service,args.spreadsheet_id,rows,missing,catalog,report)
        report.update({'applied':True,'sheet_count_after':report['sheet_count']+len(missing)})
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
