"""Collect five public sources and send sanitized results to the dedicated Worker."""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from payload_export import make_payload, FACILITIES

ENDPOINT = 'https://rehearsal-availability-api.takumanamikawa0118.workers.dev/ingest'
MAX_BYTES = 256 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the bearer token to a redirect destination.
        return None

def send(payload, token, opener=None):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Invalid token configuration')
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_BYTES:
        raise ValueError('Payload exceeds limit')
    opener = opener or urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
        'User-Agent': 'RehearsalAvailabilityUploader/0.1'})
    # No exception details or response bodies are printed: they could contain secrets.
    with opener.open(req, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError('Unexpected response')
        result = json.loads(response.read(4097))
        if result.get('accepted') is not True or type(result.get('updated')) is not bool:
            raise RuntimeError('Unexpected response')
        return result['updated']

def main():
    token = os.environ.get('INGEST_TOKEN', '')
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        print('送信設定がありません。施設の取得と送信は行いません。')
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import check_five
    check_five.main()
    report = json.loads((check_five.ROOT / 'five-results.json').read_text())
    results = {r['facility']: r for r in report['results']}
    if set(results) != FACILITIES:
        print('施設一覧が不一致のため送信を中止しました。')
        return 1
    summaries = ['\n## 空き情報サービスへの保存結果\n', '|施設|取得|保存|', '|---|---|---|']
    failed = False
    for facility, row in results.items():
        good = 'error' not in row
        rows = [r for r in check_five.parser.ROWS if r['facility'] == facility]
        try:
            payload = make_payload(facility, rows, good, report['checked_at_utc'])
        except (ValueError, KeyError, TypeError):
            good = False
            payload = make_payload(facility, [], False, report['checked_at_utc'])
        saved = False
        for attempt in range(2):
            try:
                updated = send(payload, token)
                saved = True
                status = '保存済み' if updated else '同一・古い報告のため更新なし'
                break
            except urllib.error.HTTPError as exc:
                retry = exc.code in (500, 502, 503, 504)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                retry = True
            except Exception:
                retry = False
            if not retry or attempt == 1:
                status = '送信失敗（詳細・トークンは非表示）'
                break
            time.sleep(5)
        failed |= not saved or not good
        summaries.append(f"|{row['name']}|{'解析完了' if good else '未確認'}|{status}|")
        print(f"{row['name']}: {'解析完了' if good else '未確認'} / {status}")
    summaries.append('\n取得失敗の施設は未確認として保存します。直近の成功データを現在の空室として返しません。\n')
    summary = '\n'.join(summaries)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(summary)
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
