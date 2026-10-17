# 空き情報 保存・配信サービス（未公開の試作）

このブランチは後続工程の準備です。実行中の5施設の毎時検証には変更を加えません。
D1とWorkerの初期設定は利用者が実施済みです。送受信の実環境検証と稽古ノートへの組み込みは未完了です。

## 用途

GitHubの取得処理から施設ごとのデータを受け取り、Cloudflare D1に最新の状態を保存し、アプリに返します。
既存の端末通知用Workerとは別のWorkerとして運用する想定です。
予約表の団体名・予約表原文・座組メンバー情報は送信対象にしません。

## ファイル

- worker.mjs：Cloudflare Worker用のHTTP処理とD1の作成用SQL（SCHEMA定数）。
- worker.test.mjs：Node標準テストと実際のインメモリSQLiteによる検証。
- payload_export.py：既存解析結果から送信項目だけを取り出すアダプター。自己テスト付き。通信処理は含みません。
- collect_and_send.py：取得処理を呼び出し、原文を除いた結果を専用Workerに送信。
- sender_test.py：固定送信先・転送拒否・トークンと容量制限の検証。

## テスト（外部サイトへのアクセスなし）

Node 24以降で `node --test availability-service/worker.test.mjs`。
Python 3で `python3 availability-service/payload_export.py`。
Pythonアダプターは標準ライブラリのみ。Nodeテストも追加パッケージ不要です。
実際のCloudflare環境のCPU時間・D1接続・利用枠は未検証です。

## API

- GET /health：サービス名・スキーマ・試作段階を返します。DB疎通を示すものではありません。
- POST /ingest：AuthorizationヘッダーのBearerトークンが必要です。1施設1回ずつ送信。
- GET /availability?facility=hiranuma：指定施設の現在の検証状態を返します。

入力は schema_version=1、facility、checked_at（UTC秒単位）、result（ok/error）、cells のみ。
cellsはroom/date/period/statusのみ。失敗報告は空配列、成功報告は1～1500枠。
1送信256KiBまで。取得時刻が24時間より古い、または5分を超える未来なら拒否します。

## 誤表示を防ぐ扱い

- 新しい成功報告では最新データを置き換えます。
- 新しい失敗報告では直近の成功データを保存したまま、APIはunverifiedと空配列を返します。
- 最終成功から6時間以上経った場合もunverified。no_dataと区別します。
- 古い時刻の報告や同じ報告の再送は最新結果を上書きしません。
- 帷子のavailable_candidateは候補のまま。available_in_sourceへの変更は拒否します。
- 午前/午後/夜間のラベルを保持し、時刻の正規化はまだ行いません。
- source_checkedは「取得・解析処理が完了した」という意味です。予約可能性・全件の精度を保証しません。

## Cloudflareで後から必要になる操作

1. 無料プランで空き情報専用D1を作成し、worker.mjsのSCHEMAを実行。
2. 空き情報専用Workerを作成し、D1をDBという名前でバインド。
3. WorkerのシークレットINGEST_TOKENに十分長い専用トークンを登録。
4. 同じトークンをGitHubのリポジトリシークレットに登録（コード・ログ・URLには入れない）。
5. APP_ORIGINに https://keiko-note-9718d.web.app を設定。
6. 手動で送受信・失敗・古いデータ・実際のCPU時間を確認してから毎時検証へ接続。

認証情報の作成と登録はこのPRに含めません。
Freeの利用枠を超えて停止した場合、アプリ側で「取得できません」と表示する設計です。有料プランへの自動切替は行いません。
この設計では施設ごとに1行、合計5行の最新データだけを保持します（履歴の蓄積はしません）。

## 次の未完了事項

毎時検証の安定性確認、帷子の更新画像への対応、施設ごとの利用時間・予約受付条件の確認、
送受信の実環境検証、実環境の無料枠/CPU時間測定、
空き情報一覧画面、イベント全時間を同一室で満たす候補検索。

## 公式資料

- https://developers.cloudflare.com/d1/worker-api/prepared-statements/
- https://developers.cloudflare.com/d1/platform/limits/
- https://developers.cloudflare.com/d1/platform/pricing/

## 送信の実環境確認

availability-upload-check.ymlは試作ブランチの送信コード変更時、または手動実行時に5施設の取得と保存を行います。毎時処理とは同時実行しません。送信先は専用WorkerのHTTPS URLに固定し、転送を拒否します。INGEST_TOKENはActionsの実行ステップにだけ渡します。認証失敗は再試行しません。一時的な通信失敗のみ1回再試行します。

元の取得処理が出す概要は取得部分のみの説明です。その下の「空き情報サービスへの保存結果」で送信結果を確認してください。
