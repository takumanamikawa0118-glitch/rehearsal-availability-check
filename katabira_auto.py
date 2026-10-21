"""Image change validation; cache only sanitized cells, hashes and HTTP validators."""
import calendar,datetime,json,re,subprocess,tempfile,unicodedata
from pathlib import Path
import cv2,numpy as np
VERSION=4
CACHE=Path(__file__).parent/'.availability-cache'/'katabira.json'
def load_cache():
 try:
  d=json.loads(CACHE.read_text());return d.get('images',{}) if d.get('version')==VERSION else {}
 except (OSError,ValueError):return {}
def save_cache(images):
 CACHE.parent.mkdir(exist_ok=True);tmp=CACHE.with_suffix('.tmp');tmp.write_text(json.dumps({'version':VERSION,'images':images},ensure_ascii=False));tmp.replace(CACHE)
def ocr(image,psm=6,lang='jpn+eng',digits=False):
 with tempfile.TemporaryDirectory() as directory:
  path=Path(directory)/'image.png';cv2.imwrite(str(path),image)
  args=['tesseract',str(path),'stdout','--psm',str(psm),'-l',lang]
  if digits:args+=['-c','tessedit_char_whitelist=0123456789']
  try:return unicodedata.normalize('NFKC',subprocess.check_output(args,stderr=subprocess.DEVNULL,timeout=35).decode())
  except (OSError,subprocess.SubprocessError) as e:raise ValueError('画像の文字検証を実行できません') from e

def validate_image(path,month):
 im=cv2.imread(str(path))
 if im is None:raise ValueError('予約画像を開けません')
 h,w=im.shape[:2]
 if not(1000<=w<=2200 and 1400<=h<=3200):raise ValueError('画像の寸法が変更されています')
 gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY);binary=(gray<100).astype('uint8')
 horiz=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,round(w*.14)),np.uint8));vert=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((round(h*.15),1),np.uint8))
 def groups(a):
  ids=np.where(a)[0];return [float(np.median(x)) for x in np.split(ids,np.where(np.diff(ids)>1)[0]+1) if len(x)]
 xs=groups(vert.sum(axis=0)>h*.3);ys=groups(horiz.sum(axis=1)>w*.28)
 if len(xs)!=8 or not 90<=len(ys)<=95:raise ValueError('予約表の行・列構造が変更されています')
 step=(ys[-1]-ys[1])/93
 if not 15<=step<=19 or max(abs((y-ys[1])/step-round((y-ys[1])/step)) for y in ys[1:])>.12:raise ValueError('予約表の行間隔が変更されています')
 widths=np.diff(xs[3:])
 if max(widths)/min(widths)>1.25:raise ValueError('部屋の列構造が変更されています')
 title=re.sub(r'\s+','',ocr(im[:int(ys[0])]))
 match=re.search(r'R(\d{1,2})[.．・](\d{1,2})月',title,re.I)
 if not match:raise ValueError('画像タイトルの年月を確認できません')
 year=2018+int(match[1]);current=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).year
 if int(match[2])!=month or not current-1<=year<=current+2:raise ValueError('画像の年月と掲載ラベルが一致しません')
 strip=im[int(ys[1])+2:int(ys[-1])-2,int(xs[0])+3:int(xs[1])-3]
 g=cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY);b=(g<130).astype('uint8')*255;lines=cv2.morphologyEx(b,cv2.MORPH_OPEN,np.ones((1,20),np.uint8));b=cv2.subtract(b,lines)
 clean=cv2.copyMakeBorder(cv2.resize(255-b,None,fx=3,fy=3),20,20,20,20,cv2.BORDER_CONSTANT,value=255)
 days=[int(x) for x in re.findall(r'(?m)^\s*(\d{1,2})\s*$',ocr(clean,lang='eng',digits=True))]
 last=0;matched=[];count=calendar.monthrange(year,month)[1]
 for day in days:
  if last<day<=count:matched.append(day);last=day
 if len(matched)<count-4 or matched[0]!=1 or matched[-1]!=count:raise ValueError('日付列の並びを確認できません')
 return year

def sanitized(rows):return [{k:r[k] for k in ['facility','room','date','period','status']} for r in rows]
def validate_cached(item,month):
 if not item or item.get('version')!=VERSION or item.get('month')!=month or not re.fullmatch('[a-f0-9]{64}',item.get('sha256','')):return False
 try:
  year=item['year'];cells=item['cells'];keys={(r['room'],r['date'],r['period']) for r in cells}
  expected={(room,f'{year}-{month:02}-{day:02}',period) for day in range(1,calendar.monthrange(year,month)[1]+1) for room in ['研修室I','研修室II','研修室III','和室'] for period in ['午前','午後','夜間']}
  return keys==expected and len(cells)==len(keys) and all(r['facility']=='katabira' and r['status'] in ['available_in_source','occupied_or_unknown','unavailable','closed'] for r in cells)
 except (KeyError,ValueError,TypeError):return False
