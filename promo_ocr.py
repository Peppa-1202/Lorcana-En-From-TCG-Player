"""Conservative local OCR of printed Promo collector codes; no paid OCR service."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

PROMO = 'Disney Lorcana Promo Cards'
PENDING = '待核查'
# Categories intentionally not hardcoded: CC1, PD1, RPH, DIS, etc. are valid.
CODE = re.compile(r'(?<![A-Z0-9])(\d{1,3}[A-Z]?)\s*/\s*([A-Z]{1,6}\d{0,2})\s*[·•.\-]\s*(EN|FR|DE|IT|ES|JA|ZH)\s*[·•.\-]\s*(\d{1,3})(?!\d)', re.I)
FULL = re.compile(r'\d{1,3}[A-Z]?/([A-Z]{1,6}\d{0,2})·(?:EN|FR|DE|IT|ES|JA|ZH)·\d{1,3}')

def candidates(text):
    text = text.replace('／','/').replace('–','-').replace('—','-').upper()
    return {f'{n}/{group}·{lang}·{edition}' for n,group,lang,edition in CODE.findall(text)}

def subtype(code):
    text=str(code).strip()
    if re.fullmatch(r'(EN|FR|DE|IT|ES|JA|ZH)·\d{1,3}',text):
        return '无编号'
    matches=[FULL.fullmatch(part.strip()) for part in text.split(';')]
    if matches and all(matches):
        groups={m.group(1) for m in matches}
        if len(groups)==1:
            return next(iter(groups))
    return PENDING

def consensus(texts):
    sets = [candidates(t) for t in texts]
    found = set().union(*sets)
    if len(found) == 1:
        value = next(iter(found))
        if sum(value in s for s in sets) >= 2:
            return value
    return PENDING

def recognize_bytes(data):
    from PIL import Image, ImageOps
    image = Image.open(BytesIO(data)).convert('RGB')
    w,h = image.size
    texts=[]
    for box,psm in [((0,.95,.55,.99),6),((0,.925,.6,1),6),((.01,.955,.60,.987),7)]:
        crop=image.crop(tuple(int(v*d) for v,d in zip(box,(w,h,w,h))))
        crop=ImageOps.grayscale(crop.resize((crop.width*4,crop.height*4)))
        buf=BytesIO();crop.save(buf,format='PNG')
        result=subprocess.run(['tesseract','stdin','stdout','--psm',str(psm),'-l','eng','-c','load_system_dawg=0','-c','load_freq_dawg=0'],input=buf.getvalue(),capture_output=True,check=True,timeout=25,env={**os.environ,'OMP_THREAD_LIMIT':'1'})
        texts.append(result.stdout.decode())
    return consensus(texts),texts

def scan(rows, image_dir=None):
    reviewed_path=Path(__file__).with_name('promo_codes_reviewed.json')
    reviewed=json.loads(reviewed_path.read_text()) if reviewed_path.exists() else {}
    targets=[r for r in rows if len(r)>1 and r[1]==PROMO]
    def one(row):
        pid=str(row[0]); existing=str(row[26]).strip() if len(row)>26 else ''
        if subtype(existing)!=PENDING:
            return pid,{'code':existing,'source':'existing-sheet'}
        try:
            if not pid.isdigit():
                raise ValueError('Invalid product ID')
            path=Path(image_dir)/f'{pid}.jpg' if image_dir else None
            if path and path.exists():
                data=path.read_bytes()
            else:
                url=f'https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg'
                with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=40) as response:
                    data=response.read(15_000_001)
                if len(data)>15_000_000:
                    raise ValueError('Oversize image')
            digest=sha256(data).hexdigest()
            cached=reviewed.get(pid,{})
            if cached.get('sha256')==digest:
                return pid,{'code':cached['code'],'source':'image-reviewed','sha256':digest}
            code,texts=recognize_bytes(data)
            return pid,{'code':code,'source':'ocr-consensus' if code!=PENDING else 'needs-review','sha256':digest,'ocr':texts}
        except Exception as exc:
            return pid,{'code':PENDING,'source':'error','error':str(exc)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=dict(pool.map(one,targets))
    return results

def summary(results):
    counts=Counter(subtype(r['code']) for r in results.values())
    return {'total':len(results),'recognized':len(results)-counts[PENDING],'pending':counts[PENDING],'subtypes':dict(sorted(counts.items()))}
