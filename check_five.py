"""Hourly, read-only five-facility research check. No production writes."""
import collections
import errno
import hashlib
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import parse_probe as parser
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
RAW = ROOT / 'raw'
NAMES = dict(hiranuma='平沼', tobe='戸部', karuizawa='軽井沢', katabira='帷子', kikuna='菊名')
URLS = dict(
    hiranuma='https://hiranuma.nkrsk.info/room/',
    tobe='https://tobe.nkrsk.info/',
    karuizawa='https://karuizawa.nkrsk.info/',
    katabira='https://katabira.hodogaya-kumin.com/smartphone/yoyaku.html',
    kikuna='https://www.kouhoku-ksk.or.jp/kikunacomiha/smartphone/yoyaku/?type=on&roomlot=1572497566',
)
BASE = json.loads((ROOT / 'baseline.json').read_text())
FETCHES = []
ATTEMPTS = []
HTTP_VALIDATORS = {}
RESPONSE_VALIDATORS = {}

def request_policy(url):
    # The two intermittently slow sources get a longer, bounded budget.
    slow_source = urllib.parse.urlsplit(url).hostname in {urllib.parse.urlsplit(URLS[x]).hostname for x in ['katabira', 'kikuna']}
    return (40, (0, 10, 20)) if slow_source else (25, (0, 5))

def failure_kind(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(exc, urllib.error.HTTPError):
        return 'http_' + str(exc.code)
    if isinstance(reason, ssl.SSLError):
        return 'tls'
    if isinstance(reason, socket.gaierror):
        return 'dns'
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'timeout'
    if isinstance(reason, ConnectionError):
        return 'connection'
    if isinstance(reason, OSError) and reason.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return 'network_unreachable'
    return type(reason).__name__


def fetch_once(url, filename, origin):
    if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(origin).hostname:
        raise ValueError('参照先のホストが変わりました。手動確認が必要です')
    started = time.monotonic()
    headers={'User-Agent': 'RehearsalAvailabilityProbe/0.3 (read-only research)'}
    headers.update(HTTP_VALIDATORS.get(url,{}))
    req = urllib.request.Request(url, headers=headers)
    timeout, _ = request_policy(url)
    phase = 'connect_tls_or_headers'
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if urllib.parse.urlsplit(response.url).hostname != urllib.parse.urlsplit(origin).hostname:
                raise ValueError('転送先の確認が必要です')
            RESPONSE_VALIDATORS[url]={k:response.headers.get(v) for k,v in [('etag','ETag'),('modified','Last-Modified')] if response.headers.get(v)}
            phase = 'body_read'
            body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError('取得容量の上限を超えました')
            digest = hashlib.sha256(body).hexdigest()
            (RAW / filename).write_bytes(body)
            FETCHES.append(dict(file=filename, http=response.status, bytes=len(body), sha256=digest,
                                seconds=round(time.monotonic()-started, 3)))
            return body, digest
    except urllib.error.HTTPError as exc:
        if exc.code==304 and url in HTTP_VALIDATORS and urllib.parse.urlsplit(exc.url).hostname==urllib.parse.urlsplit(origin).hostname:
            FETCHES.append(dict(file=filename,http=304,bytes=0))
            return b'',None
        exc.fetch_phase=phase
        raise
    except Exception as exc:
        exc.fetch_phase = phase
        raise

def fetch(url, filename, origin):
    """Bounded retries for transient errors; never retry denials, rate limits or TLS errors."""
    timeout, delays = request_policy(url)
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        started = time.monotonic()
        try:
            result = fetch_once(url, filename, origin)
            ATTEMPTS.append(dict(file=filename, attempt=attempt, result='ok',
                                 timeout_seconds=timeout, seconds=round(time.monotonic()-started,3)))
            return result
        except Exception as exc:
            kind = failure_kind(exc)
            transient = kind in ('timeout', 'dns', 'connection', 'network_unreachable', 'http_500', 'http_502', 'http_503', 'http_504')
            ATTEMPTS.append(dict(file=filename, attempt=attempt, result='error',
                                 error=f'{type(exc).__name__}: {exc}', kind=kind,
                                 phase=getattr(exc, 'fetch_phase', 'validation'), timeout_seconds=timeout,
                                 seconds=round(time.monotonic()-started,3)))
            if attempt == len(delays) or not transient:
                raise

def check_pdf(facility):
    origin = URLS[facility]
    body, _ = fetch(origin, facility+'.html', origin)
    soup = BeautifulSoup(body, 'html.parser')
    links = {urllib.parse.urljoin(origin, a['href']) for a in soup.select('a[href]')
             if urllib.parse.urlsplit(a['href']).path.endswith('/room.pdf')}
    if len(links) != 1:
        raise ValueError('予約PDFを一意に特定できません')
    data, digest = fetch(links.pop(), facility+'.pdf', origin)
    if not data.startswith(b'%PDF-'):
        raise ValueError('PDFではありません')
    parser.parse_pdf(facility)
    return digest

def check_katabira():
    origin = URLS['katabira']
    body, _ = fetch(origin, 'katabira.html', origin)
    text = body.decode('cp932', errors='strict')
    soup = BeautifulSoup(text, 'html.parser')
    images = []
    seen = set()
    reviewed = {x['sha256']: x for x in BASE['katabira_images']}
    for image in soup.select('img[src]'):
        src = image['src']
        if 'dataimge' not in src or not urllib.parse.urlsplit(src).path.lower().endswith('.jpg'):
            continue
        link = urllib.parse.urljoin(origin, src)
        if link in seen:
            continue
        seen.add(link)
        label = re.fullmatch(r'(\d{1,2})月予約状況', image.get('alt', '').strip())
        if not label or not 1 <= int(label[1]) <= 12:
            raise ValueError('画像の対象月を確認できません')
        images.append((link, int(label[1])))
    if not 1 <= len(images) <= 12:
        raise ValueError('予約画像数を確認できません')
    import katabira_auto as auto
    cache=auto.load_cache();updated={};months=set();reused=0;analyzed=0
    for i,(link,month) in enumerate(images):
        old=cache.get(link);valid=auto.validate_cached(old,month)
        if valid:HTTP_VALIDATORS[link]={k:old[v] for k,v in [('If-None-Match','etag'),('If-Modified-Since','modified')] if old.get(v)}
        _,digest=fetch(link,f'katabira-{i}.jpg',origin)
        if valid and (digest is None or digest==old.get('sha256')):
            parser.ROWS.extend(old['cells']);entry={**old,**RESPONSE_VALIDATORS.get(link,{})};reused+=1
        else:
            year=auto.validate_image(RAW/f'katabira-{i}.jpg',month)
            before=len(parser.ROWS);parser.parse_image(i,year=year,month=month)
            cells=parser.ROWS[before:]
            if digest in reviewed:
                for sample in (x for x in BASE['samples'] if x['facility']=='katabira' and x['date'].startswith(f'{year}-{month:02}-')):
                    expected='available_in_source' if sample['expected']=='available_candidate' else sample['expected']
                    hits=[r for r in cells if all(r[k]==sample[k] for k in ['date','room','period'])]
                    if len(hits)!=1 or (hits[0]['status']!=expected and not(hits[0]['status']=='closed' and expected=='unavailable')):raise ValueError('確認済み画像サンプルと不一致です')
            entry=dict(version=auto.VERSION,sha256=digest,year=year,month=month,cells=auto.sanitized(cells),**RESPONSE_VALIDATORS.get(link,{}));analyzed+=1
        key=f"{entry['year']}-{month:02}"
        if key in months:raise ValueError('画像の対象年月が重複しています')
        months.add(key);updated[link]=entry
    auto.save_cache(updated)
    parser.METRICS.append(dict(facility='katabira',images_analyzed=analyzed,images_reused=reused))
    return None

def check_kikuna():
    origin = URLS['kikuna']
    body, _ = fetch(origin, 'kikuna.html', origin)
    soup = BeautifulSoup(body.decode('cp932'), 'html.parser')
    rooms = list(dict.fromkeys(m[1] for tag in soup.select('input[onclick]')
                 if (m := re.search(r'roomlot=(\d+)', tag['onclick']))))
    if set(rooms) != {'1572497566', '1572497573', '1572497588'}:
        raise ValueError('部屋一覧が変わりました')
    form = soup.select_one('form[name=cnext2]')
    params = {x['name']: x.get('value', '') for x in form.select('input[name]')}
    if not params.get('s'):
        raise ValueError('翌月リンクを確認できません')
    for room in rooms:
        for offset in [0, 1]:
            filename = 'kikuna.html'
            if room != '1572497566' or offset:
                query = {'type': 'on', 'roomlot': room}
                if offset:
                    query['s'] = params['s']
                filename = f'kikuna-{room}-{offset}.html'
                fetch(origin.split('?')[0]+'?'+urllib.parse.urlencode(query), filename, origin)
            parser.parse_kikuna(filename)
    return None

def main():
    RAW.mkdir(exist_ok=True)
    # Never reuse a source from an earlier run after a failed fetch.
    for path in RAW.iterdir():
        if path.is_file():
            path.unlink()
    parser.ROWS.clear()
    parser.METRICS.clear()
    FETCHES.clear()
    ATTEMPTS.clear()
    results = []
    for facility in URLS:
        start = time.monotonic()
        before = len(parser.ROWS)
        row = dict(facility=facility, name=NAMES[facility])
        try:
            digest = check_pdf(facility) if facility in ['hiranuma','tobe','karuizawa'] else (
                check_katabira() if facility == 'katabira' else check_kikuna())
            cells = parser.ROWS[before:]
            if not cells:
                raise ValueError('解析結果が0件です')
            keys = [(x['room'], x['date'], x['period']) for x in cells]
            if len(set(keys)) != len(keys):
                raise ValueError('日付・部屋・時間帯が重複しています')
            row.update(result='解析完了（研究用）', cells=len(cells),
                       counts=dict(collections.Counter(x['status'] for x in cells)),
                       months=sorted({x['date'][:7] for x in cells}))
            # Snapshot assertions only apply while the exact PDF is unchanged.
            if digest and digest == BASE['hashes'].get(facility):
                samples = [s for s in BASE['samples'] if s['facility'] == facility]
                for s in samples:
                    hits = [x for x in cells if all(x[k] == s[k] for k in ['date','room','period'])]
                    if len(hits) != 1 or (hits[0]['status'] != s['expected'] and not(hits[0]['status']=='closed' and s['expected']=='unavailable')):
                        raise ValueError('既存の目視確認サンプルと不一致です')
                row['baseline_samples_passed'] = len(samples)
        except Exception as exc:
            del parser.ROWS[before:]
            row.update(result='取得・解析できず（空室とは判定しません）', cells=0,
                       error=f'{type(exc).__name__}: {exc}')
        row['seconds'] = round(time.monotonic()-start, 3)
        row['retries'] = sum(1 for a in ATTEMPTS if a['attempt'] > 1 and
                             a['file'].startswith(facility))
        results.append(row)
        print(NAMES[facility], row['result'], row['cells'], row['seconds'])
    report = dict(research_only=True, checked_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  run_id=os.getenv('GITHUB_RUN_ID'), event=os.getenv('GITHUB_EVENT_NAME','local'),
                  results=results, fetches=FETCHES, attempts=ATTEMPTS, metrics=parser.METRICS)
    (ROOT/'five-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    lines = ['# 5施設 取得・解析確認', '', '研究用の検証です。予約可能性や全件の読み取り精度を保証するものではありません。', '',
             f"検証時刻（UTC）: {report['checked_at_utc']}", '',
             '|施設|結果|解析枠数|再試行回数|秒|', '|---|---|---:|---:|---:|']
    for r in results:
        lines.append(f"|{r['name']}|{r['result']}|{r['cells']}|{r['retries']}|{r['seconds']}|")
    lines += ['', '帷子は画像更新時だけ年月・日付列・表構造・空欄を自動検証します。検証できなければ未確認です。この概要は取得・解析部分の結果です。送信を伴う実行では、下の保存結果も確認してください。', '',
              '取得失敗時は当該施設の解析結果を破棄します。団体名や予約表原文はログに出しません。', '',
              '<details><summary>詳細（展開してください）</summary>', '', '```json',
              json.dumps(report,ensure_ascii=False,indent=2), '```', '</details>']
    summary='\n'.join(lines)
    (ROOT/'SUMMARY.md').write_text(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
            stream.write(summary)
    # Keep the full summary, then make failed facilities visible in Actions.
    return 1 if any('error' in row for row in results) else 0

if __name__ == '__main__':
    raise SystemExit(main())
