"""Snapshot parsers. Research only; unknowns never become available.
Run after fetch_probe.py and download_assets.py. Does not contact Firebase/Cloudflare.
"""
from pathlib import Path
import json,re,time,calendar,datetime,collections
import pdfplumber,numpy as np,cv2
from bs4 import BeautifulSoup
R=Path(__file__).parent
ROWS=[];METRICS=[]
def put(facility,room,date,period,status,reason,page,raw='',**extra):
 ROWS.append(dict(facility=facility,room=room,date=date,period=period,status=status,reason=reason,page=page,raw=raw,**extra))
def center(b):return ((b[0]+b[2])/2,(b[1]+b[3])/2)
def colored(p,b,kind):
 x,y=center(b)
 for r in p.rects:
  if r['fill'] and r['x0']<=x<=r['x1'] and r['top']<=y<=r['bottom']:
   c=r['non_stroking_color']; c=(c,c,c) if isinstance(c,(int,float)) else c
   if c and len(c)==3 and ((kind=='dark' and max(c)<.65) or (kind=='yellow' and c[0]>.8 and c[1]>.8 and c[2]<.3)):return True
 return False
def diagonal(p,b):
 for c in p.curves:
  x,y=center((c['x0'],c['top'],c['x1'],c['bottom']))
  if b[0]+1<x<b[2]-1 and b[1]+1<y<b[3]-1 and c['width']>8 and c['height']>8:return True
 return False
def parse_pdf(n):
 start=time.perf_counter();before=len(ROWS)
 with pdfplumber.open(R/'raw'/f'{n}.pdf') as pdf:
  for pi,p in enumerate(pdf.pages,1):
   text=p.extract_text();table=p.find_tables()[0];data=table.extract();assert table.bbox[2]-table.bbox[0]>300
   if n=='karuizawa':
    y=2018+int(re.search(r'令和\s*([０-９0-9８]+)年',text)[1]);m=int(re.search(r'\n(\d+)月\s*令和',text)[1])
   else:
    y=int(re.search(r'(20\d\d)/',text)[1]);m=int(re.search(r'(\d+)\s*月予約状況',text)[1])
   if n=='hiranuma':
    room='';daycols={};assert len(data) in (16,)
    for ri,row in enumerate(data):
     if row[1]=='日':daycols={ci:int(v) for ci,v in enumerate(row) if ci>=2 and v and v.isdigit()};continue
     if row[1] not in ['午前','午後','夜間']:continue
     room=row[0] or room
     for ci,day in daycols.items():
      b=table.rows[ri].cells[ci];v=row[ci];assert b and v is not None
      if diagonal(p,b):status,why='unavailable','diagonal_closure'
      elif v.strip():status,why='occupied','printed_reservation'
      elif '抽選日翌日' in text:status,why='unknown','lottery_release_not_verified'
      else:status,why='available_in_source','blank_confirmed_table_cell'
      put(n,room,f'{y}-{m:02}-{day:02}',row[1],status,why,pi)
   elif n=='tobe':
    rooms=['レクリエーション','集会室1','和室','集会室2'];assert len(data[2])==14
    for ri,row in enumerate(data):
     match=re.fullmatch(r'(\d+)日',row[0] or '')
     if not match:continue
     day=int(match[1]);assert 1<=day<=calendar.monthrange(y,m)[1]
     for ci in range(2,14):
      v=row[ci];b=table.rows[ri].cells[ci];assert b and v is not None
      if colored(p,b,'dark'):status,why='unavailable','closure_shading'
      elif v.strip()=='優':status,why='unavailable','priority_group'
      elif v.strip() in ['―','－','-']:status,why='unavailable','unavailable_mark'
      elif v.strip()=='×':status,why='occupied','reserved_mark'
      elif v.strip():status,why='unknown','unexpected_mark'
      else:status,why='available_in_source','blank_confirmed_table_cell'
      put(n,rooms[(ci-2)//3],f'{y}-{m:02}-{day:02}',['午前','午後','夜間'][(ci-2)%3],status,why,pi)
   else:
    assert data[1][2:]==['ホール','和室','ホール','和室','ホール','和室']
    for ri,row in enumerate(data[2:],2):
     if not row[0] or not row[0].isdigit():continue
     day=int(row[0]);date=f'{y}-{m:02}-{day:02}';closed='休館' in ''.join(x or '' for x in row) or row[1] in ['月','木'] or (m==12 and day>=29)
     for ci in range(2,8):
      v=row[ci];b=table.rows[ri].cells[ci]
      if closed:status,why='closed','closed_day_or_merged_closure'
      elif b is None or v is None:status,why='unknown','merged_cell'
      elif v.strip()=='×':status,why='occupied','reserved_mark'
      elif v.strip():status,why='unavailable','facility_program'
      else:status,why='available_in_source','blank_confirmed_table_cell'
      put(n,['ホール','和室'][(ci-2)%2],date,['午前','午後','夜間'][(ci-2)//2],status,why,pi)
 METRICS.append({'facility':n,'parse_seconds':round(time.perf_counter()-start,4),'cells':len(ROWS)-before})
def parse_kikuna(filename):
 before=len(ROWS);p=BeautifulSoup((R/'raw'/filename).read_bytes().decode('cp932'),'html.parser');text=p.get_text(' ',strip=True);year=int(re.search(r'更新日：\s*(\d{4})/',text)[1]);room=re.search(r'\d+月\s*(.+?)の空き状況',text)[1];date=None
 for tr in p.select('table tr'):
  td=tr.find_all('td',recursive=False)
  if len(td)==1:
   mm=re.match(r'(\d+)/(\d+)',td[0].get_text(strip=True));date=f'{year}-{int(mm[1]):02}-{int(mm[2]):02}' if mm else date
  elif len(td)==2 and date:
   label=td[0].get_text(' ',strip=True);v=td[1].get_text(' ',strip=True)
   if not re.match('午前|午後|夜間',label):continue
   if v.startswith('○'):status,why='occupied','reserved_mark'
   elif v.startswith(('×','●')):status,why='unavailable','closed_or_unavailable_mark'
   elif v=='空':status,why='available_in_source','explicit_available_mark'
   elif not v:status,why='available_in_source','blank_available_confirmed_by_user'
   else:status,why='occupied','reservation_name_present'
   put('kikuna',room,date,label.split()[0],status,why,1)
 assert len(ROWS)-before in [90,93]
 METRICS.append({'facility':'kikuna','source':filename,'cells':len(ROWS)-before,'note':'空白は空室、団体名の記載は予約済み。利用者確認と10月6日A室の直接照合による。'})
def parse_image(i, year=2026, month=None):
 t=time.perf_counter();im=cv2.imread(str(R/'raw'/f'katabira-{i}.jpg'));h,w=im.shape[:2];gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY);binary=(gray<100).astype('uint8')
 horiz=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,200),np.uint8));vert=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((300,1),np.uint8))
 def groups(a):
  ids=np.where(a)[0];return [float(np.median(x)) for x in np.split(ids,np.where(np.diff(ids)>1)[0]+1) if len(x)]
 xs=groups(vert.sum(axis=0)>600);ys=groups(horiz.sum(axis=1)>400);assert len(xs)==8 and len(ys)>=90
 # Current source has 31 template days even in November. Refuse a different geometry.
 top=ys[1];bottom=ys[-1];step=(bottom-top)/93;assert 15<step<19
 m=month if month is not None else 10+i;y=year;count=calendar.monthrange(y,m)[1];assert (R/'raw'/'katabira.html').read_bytes().find(f'{m}月予約状況'.encode('cp932'))>=0
 debug=[]
 for day in range(1,count+1):
  for slot in range(3):
   ri=(day-1)*3+slot;y0=int(round(top+ri*step));y1=int(round(top+(ri+1)*step))
   for room in range(4):
    x0=int(xs[3+room]);x1=int(xs[4+room]);crop=im[y0+4:y1-3,x0+5:x1-5];g=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY);ink=(g<110);frac=float(ink.mean());yellow=float(((crop[:,:,2]>180)&(crop[:,:,1]>180)&(crop[:,:,0]<120)).mean());rgb=crop.astype('int16');green=float(((rgb[:,:,1]>170)&(rgb[:,:,2]>150)&(rgb[:,:,0]>140)&(rgb[:,:,1]-rgb[:,:,2]>4)&(rgb[:,:,1]-rgb[:,:,0]>10)).mean())
    if datetime.date(y,m,day).weekday() in [1,4]:status,why='closed','tuesday_friday_closure'
    elif green>.4:status,why='closed','green_closure_shading'
    elif yellow>.4:status,why='unavailable','yellow_program_or_merged_area'
    elif frac<.002:status,why='available_in_source','validated_blank_image_cell'
    else:status,why='occupied_or_unknown','ink_present_not_assumed_blank'
    put('katabira',['研修室I','研修室II','研修室III','和室'][room],f'{y}-{m:02}-{day:02}',['午前','午後','夜間'][slot],status,why,i+1,ink_fraction=round(frac,4))
 METRICS.append({'facility':f'katabira-{m}','parse_seconds':round(time.perf_counter()-t,4),'cells':count*12,'warning':'Layout-specific prototype; available_candidate not approved availability. Year/month labels require confirmation on future files.'})
if __name__=='__main__':
 t=time.perf_counter()
 for n in ['hiranuma','tobe','karuizawa']:parse_pdf(n)
 for file in [R/'raw'/'kikuna.html',*sorted((R/'raw').glob('kikuna-157*.html'))]:parse_kikuna(file.name)
 for i in [0,1]:parse_image(i)
 (R/'normalized-snapshot.json').write_text(json.dumps({'research_only':True,'booking_not_verified':True,'cells':ROWS},ensure_ascii=False,indent=2))
 (R/'parse-metrics.json').write_text(json.dumps(METRICS,ensure_ascii=False,indent=2));print(json.dumps(METRICS,ensure_ascii=False,indent=2));print('total',len(ROWS),'elapsed',round(time.perf_counter()-t,3));print(collections.Counter((x['facility'],x['status']) for x in ROWS))
