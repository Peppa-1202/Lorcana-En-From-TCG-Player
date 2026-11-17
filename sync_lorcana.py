"""TCGplayer Lorcana Cards catalogue audit and append-only Google Sheets sync."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import unescape
import json
import os
from pathlib import Path
import re
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

SEARCH_URL = 'https://mp-search-api.tcgplayer.com/v1/search/request'
DEFAULT_SHEET = ''
HEADERS = ['商品 ID','系列名称','系列代码','卡牌编号','英文卡名','稀有度','墨水颜色','墨水费用','可放墨水区','卡牌类型','角色分类','力量','意志力','传说值','移动费用','效果文本','背景文本','所属作品','市场价 USD','最低价 USD','最低含运费 USD','在售 listings 数','商品链接','卡图链接','采集日期','备注']

BASE_HEADERS = HEADERS.copy()
CODE_HEADERS = BASE_HEADERS + ['卡牌代码']
HEADERS = CODE_HEADERS + ['促销卡系列代码']

def search(start=0, size=50, set_slug=None):
    term = {'productLineName':['lorcana-tcg'], 'productTypeName':['Cards']}
    if set_slug:
        term['setName'] = [set_slug]
    payload = {'algorithm':'sales_synonym_v2','from':start,'size':size,
               'filters':{'term':term,'range':{},'match':{}},
               'listingSearch':{'filters':{'term':{},'range':{},'exclude':{}},'context':{'cart':{}},'size':0},
               'context':{'cart':{},'shippingCountry':'US'},'sort':{'field':'product-name','order':'asc'}}
    for attempt in range(4):
        try:
            req = urllib.request.Request(SEARCH_URL, data=json.dumps(payload).encode(), headers={
                'Content-Type':'application/json','User-Agent':'Mozilla/5.0', 'Origin':'https://www.tcgplayer.com'})
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            if data.get('errors'):
                raise RuntimeError(str(data['errors']))
            result = data['results'][0]
            if 'totalResults' not in result or not isinstance(result.get('results'), list):
                raise RuntimeError('Unexpected search schema')
            return result
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400,401,403,404):
                raise
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def collect_catalog():
    first = search(size=1)
    total = int(first['totalResults'])
    sets = first['aggregations']['setName']
    if not total or sum(int(s['count']) for s in sets) != total:
        raise RuntimeError('Catalogue/set totals disagree; refusing partial snapshot')
    def one_set(s):
        expected = int(s['count'])
        found = {}
        offset = 0
        while offset < expected:
            page = search(start=offset, set_slug=s['urlValue'])
            if int(page['totalResults']) != expected or not page['results']:
                raise RuntimeError('Catalogue changed or empty page: ' + s['value'])
            for p in page['results']:
                if p.get('setName') != s['value'] or int(p.get('productLineId',0)) != 71 or p.get('sealed') is not False:
                    raise RuntimeError('Search filter mismatch')
                pid = str(int(p['productId']))
                if pid in found:
                    raise RuntimeError('Repeated product across pages: ' + pid)
                found[pid] = p
            offset += len(page['results'])
        if len(found) != expected:
            raise RuntimeError('Incomplete set: ' + s['value'])
        print(f"Checked {s['value']}: {len(found)}", flush=True)
        return found
    products = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for group in pool.map(one_set, sets):
            if products.keys() & group.keys():
                raise RuntimeError('Product occurs in multiple sets')
            products.update(group)
    final = search(size=1)
    counts = lambda x: {s['urlValue']:int(s['count']) for s in x['aggregations']['setName']}
    if len(products) != total or final['totalResults'] != total or counts(first) != counts(final):
        raise RuntimeError('Catalogue changed during scan; rerun before writing')
    return products

def plain(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return ';'.join(map(str, value))
    value = re.sub(r'<br\s*/?>', '\n', str(value), flags=re.I)
    value = re.sub(r'</p\s*>', '\n', value, flags=re.I)
    return unescape(re.sub(r'<[^>]+>', '', value)).strip()

def to_row(p, codes):
    a = p.get('customAttributes', {})
    pid = str(int(p['productId']))
    date = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    keys = ['number','inkType','costInk','inkwellIcononCard','cardType','classification','strength','willpower','loreValue','moveCost','description','flavorText','property']
    v = {k:plain(a.get(k)) for k in keys}
    return [pid,p['setName'],codes.get(p['setName']) or plain(p.get('setCode')),v['number'],p['productName'],p.get('rarityName',''),v['inkType'],v['costInk'],v['inkwellIcononCard'],v['cardType'],v['classification'],v['strength'],v['willpower'],v['loreValue'],v['moveCost'],v['description'],v['flavorText'],v['property'],*[p.get(k,'') if p.get(k) is not None else '' for k in ['marketPrice','lowestPrice','lowestPriceWithShipping','totalListings']],f'https://www.tcgplayer.com/product/{pid}',f'https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg',date,'价格为采集快照；卡图链接按商品 ID 生成；完整性核查补录']

def compare(rows, products):
    ids = [str(r[0]).strip() for r in rows[1:] if r and r[0]]
    duplicates = sorted(k for k,v in Counter(ids).items() if v > 1)
    if duplicates:
        raise RuntimeError('Duplicate IDs in Sheet: ' + ','.join(duplicates))
    if any(not x.isdigit() for x in ids):
        raise RuntimeError('Invalid product ID in Sheet')
    codes = {}
    for r in rows[1:]:
        if len(r)>2 and r[1] and r[2]:
            if r[1] in codes and codes[r[1]] != str(r[2]):
                raise RuntimeError('Conflicting series code: ' + r[1])
            codes[r[1]] = str(r[2])
    missing = sorted(products.keys() - set(ids), key=int)
    extra = sorted(set(ids) - products.keys(), key=int)
    return [to_row(products[k], codes) for k in missing], {'website_count':len(products),'sheet_count':len(ids),'missing_ids':missing,'sheet_only_ids':extra,'duplicate_ids':duplicates}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Append missing rows and refresh directory; default is read-only')
    parser.add_argument('--spreadsheet-id', default=os.getenv('SPREADSHEET_ID',DEFAULT_SHEET))
    parser.add_argument('--existing-json', help='Offline existing rows JSON; no Google credentials needed')
    parser.add_argument('--catalog-json', help='Use an already captured catalogue for offline audit only')
    parser.add_argument('--output', default='output')
    args = parser.parse_args()
    if args.apply and (args.existing_json or args.catalog_json):
        parser.error('--apply requires fresh live Sheet and catalogue reads')
    if not args.existing_json and not args.spreadsheet_id:
        parser.error('Set SPREADSHEET_ID or --spreadsheet-id')
    out = Path(args.output); out.mkdir(parents=True,exist_ok=True)
    service = None
    if args.existing_json:
        rows = json.loads(Path(args.existing_json).read_text(encoding='utf-8'))
    else:
        from sheets_sync import connect, read_rows
        service = connect()
        rows = read_rows(service, args.spreadsheet_id)
        if rows[0] not in (BASE_HEADERS, CODE_HEADERS, HEADERS):
            raise RuntimeError('Sheet headers changed; refusing write')
    products = json.loads(Path(args.catalog_json).read_text(encoding='utf-8')) if args.catalog_json else collect_catalog()
    (out/'catalog.json').write_text(json.dumps(products,ensure_ascii=False),encoding='utf-8')
    missing, report = compare(rows, products)
    (out/'missing_rows.json').write_text(json.dumps(missing,ensure_ascii=False,indent=2),encoding='utf-8')
    from promo_ocr import scan, summary
    promo = scan(rows[1:] + missing)
    (out/'promo_ocr.json').write_text(json.dumps(promo,ensure_ascii=False,indent=2),encoding='utf-8')
    report['promo_codes'] = summary(promo)
    report['checked_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    report['applied'] = False
    if args.apply:
        from sheets_sync import apply_missing
        apply_missing(service,args.spreadsheet_id,rows,missing,promo)
        report['applied'] = True
        report['sheet_count_after'] = report['sheet_count'] + len(missing)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
