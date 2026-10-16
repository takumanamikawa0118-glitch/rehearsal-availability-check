"""Manual, read-only five-facility research check. No production writes."""
import collections
import hashlib
import json
import os
import re
import time
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

def fetch(url, filename, origin):
    if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(origin).hostname:
        raise ValueError('参照先のホストが変わりました。手動確認が必要です')
    started = time.monotonic()
    req = urllib.request.Request(url, headers={'User-Agent': 'RehearsalAvailabilityProbe/0.3 (read-only research)'})
    with urllib.request.urlopen(req, timeout=25) as response:
        if urllib.parse.urlsplit(response.url).hostname != urllib.parse.urlsplit(origin).hostname:
            raise ValueError('転送先の確認が必要です')
        body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError('取得容量の上限を超えました')
        digest = hashlib.sha256(body).hexdigest()
        (RAW / filename).write_bytes(body)
        FETCHES.append(dict(file=filename, http=response.status, bytes=len(body), sha256=digest,
                            seconds=round(time.monotonic()-started, 3)))
        return body, digest

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
    links = list(dict.fromkeys(urllib.parse.urljoin(origin, el.get('src', el.get('href', '')))
                 for el in soup.select('[src], a[href]')
                 if 'dataimge' in el.get('src', el.get('href', ''))
                 and urllib.parse.urlsplit(el.get('src', el.get('href', ''))).path.endswith('.jpg')))
    if len(links) != 2:
        raise ValueError('画像数が変わりました。対象月と配置の再確認が必要です')
    # The image parser is only validated against these exact two originals.
    for i, link in enumerate(links):
        _, digest = fetch(link, f'katabira-{i}.jpg', origin)
        if digest != BASE['hashes'].get(f'katabira-{i}'):
            raise ValueError('画像が更新されました。年月・配置・読み取り精度を再確認します')
        parser.parse_image(i)
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
                    if len(hits) != 1 or hits[0]['status'] != s['expected']:
                        raise ValueError('既存の目視確認サンプルと不一致です')
                row['baseline_samples_passed'] = len(samples)
        except Exception as exc:
            del parser.ROWS[before:]
            row.update(result='取得・解析できず（空室とは判定しません）', cells=0,
                       error=f'{type(exc).__name__}: {exc}')
        row['seconds'] = round(time.monotonic()-start, 3)
        results.append(row)
        print(NAMES[facility], row['result'], row['cells'], row['seconds'])
    report = dict(research_only=True, checked_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  results=results, fetches=FETCHES, metrics=parser.METRICS)
    (ROOT/'five-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    lines = ['# 5施設 取得・解析確認', '', '研究用の検証です。予約可能性や全件の読み取り精度を保証するものではありません。', '',
             '|施設|結果|解析枠数|秒|', '|---|---|---:|---:|']
    for r in results:
        lines.append(f"|{r['name']}|{r['result']}|{r['cells']}|{r['seconds']}|")
    lines += ['', '帷子の空白は「空室候補」です。公開アプリへの反映・定期実行・データ保存サービスへの送信は行いません。', '',
              '取得失敗時は当該施設の解析結果を破棄します。団体名や予約表原文はログに出しません。', '',
              '<details><summary>詳細（展開してください）</summary>', '', '```json',
              json.dumps(report,ensure_ascii=False,indent=2), '```', '</details>']
    summary='\n'.join(lines)
    (ROOT/'SUMMARY.md').write_text(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
            stream.write(summary)

if __name__ == '__main__':
    main()
