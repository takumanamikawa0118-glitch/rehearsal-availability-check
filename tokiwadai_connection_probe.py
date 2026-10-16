"""Manual, read-only connection test. Standard-library only; no credentials required."""
import datetime,hashlib,html,json,os,re,time,urllib.request,urllib.error
from html.parser import HTMLParser
from pathlib import Path
URLS=['https://tokiwadai-ch.roukyou.gr.jp/','https://tokiwadai-ch.roukyou.gr.jp/?page_id=15']
class Text(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.tables=0
 def handle_data(self,data):self.parts.append(data)
 def handle_starttag(self,tag,attrs):self.tables+=tag=='table'
def inspect_html(body):
 p=Text();p.feed(body.decode('utf-8',errors='replace'));text=' '.join(p.parts)
 return {'site_name_found':'常盤台コミュニティハウス' in text,'reservation_heading_found':'集会室予約状況' in text,'html_tables':p.tables,'reservation_months':list(dict.fromkeys(re.findall(r'20\d{2}\s*年\s*\d{1,2}\s*月\s*予約状況',text)))[:6]}
def fetch(url):
 result={'url':url,'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};start=time.perf_counter()
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'RehearsalAvailabilityProbe/0.2 (read-only availability research)','Cache-Control':'no-cache'})
  with urllib.request.urlopen(req,timeout=15) as response:
   body=response.read(1_000_001);result.update(http_status=response.status,final_url=response.url,bytes=len(body),content_type=response.headers.get('Content-Type',''))
   if len(body)>1_000_000:result['error']='response_too_large'
   elif response.status==200 and 'html' in result['content_type'].lower():result.update(inspect_html(body),sha256=hashlib.sha256(body).hexdigest())
   else:result['error']='unexpected_response'
 except urllib.error.HTTPError as e:result.update(http_status=e.code,error='HTTP '+str(e.code))
 except Exception as e:result.update(error=type(e).__name__+': '+str(e))
 result['seconds']=round(time.perf_counter()-start,3);return result
def render_summary(rows):
 lines=['# 常盤台 接続確認','', 'これは接続試験です。最新の空き情報の取得・解析成功を意味しません。','', '| 対象 | HTTP | 施設名 | 表の数 | 経過秒 |','|---|---:|---|---:|---:|']
 for i,r in enumerate(rows):lines.append('| '+('トップ' if i==0 else '予約状況')+' | '+str(r.get('http_status','エラー'))+' | '+('確認' if r.get('site_name_found') else '未確認')+' | '+str(r.get('html_tables',0))+' | '+str(r['seconds'])+' |')
 lines+=['','予約状況ページでHTTP 200、施設名、予約月、表が確認できれば次の解析検証へ進みます。403なら無理な再試行や回避をせず、通常アクセスでの取得は未成立とします。','','```json',json.dumps(rows,ensure_ascii=False,indent=2),'```']
 return '\n'.join(lines)+'\n'
if __name__=='__main__':
 rows=[fetch(url) for url in URLS];report=render_summary(rows);Path('tokiwadai-connection-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');print(report)
 summary=os.environ.get('GITHUB_STEP_SUMMARY')
 if summary:
  with open(summary,'a',encoding='utf-8') as f:f.write(report)
 # HTTP refusal is a measured result, not a crash. Workflow success does not mean site access succeeded.
