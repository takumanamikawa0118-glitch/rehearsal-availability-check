# rehearsal-availability-check

## 稽古場空き情報の継続運用（2026-10-17開始）

平沼・戸部・軽井沢・帷子・菊名の5施設をGitHub Actionsで約1時間に1回取得し、空き情報専用Cloudflare Worker経由でD1に保存します。稽古ノートは保存済みデータを参照します。常盤台は対象外です。

- five-check.yml：毎時23分の実行を要求。2026-10-20の試験終了条件は解除済みです。
- GitHubの混雑等で開始が遅延・省略されることがあります。厳密な毎時更新を保証しません。
- 無料の公開リポジトリの標準ubuntuランナーだけを利用します。非公開にすると収集ジョブは停止します。有料ランナーへの変更は行いません。
- 権限はcontents:read、トークンは送信ステップのみ、タイムアウト18分、他の取得処理との同時実行を防止します。
- 1施設につき最新1行のみ保存。団体名・予約表原文は保存・ログ公開しません。
- 保存後は公開APIで全セルを読み戻して照合します。5施設すべての一致を2026-10-17の実行#21で確認しました。
- 取得失敗または最終成功から6時間経過した情報は空きと表示しません。帷子は画像変更時に再検証が必要で、空欄は要確認候補です。

## 確認・停止・再開

Actions → Five facilities availability check → 最新の実行 → Summaryの「空き情報サービスへの保存結果」を確認します。失敗時はGitHubの通知設定も確認してください。

停止：Actionsの対象ワークフローのメニューからDisable workflow。再開：Enable workflow。リポジトリ変数AVAILABILITY_PAUSEDを文字列trueにしても停止できます（再開はfalseまたは変数削除）。

公開リポジトリで60日間操作がないとGitHubにより予定実行が無効化されることがあります。その場合はワークフローの再有効化が必要です。無料枠やサービスの制限で停止した場合も自動課金への切り替えは行いません。

## 現在の反映状態

日時検索Workerは利用者がデプロイし、search_version=1の応答を確認済み。稽古ノートver1.10以降の一覧・イベント場所検索も利用者が反映確認済みです。今回の継続運用設定のためのFirebase再デプロイやCloudflare再設定は不要です。

[API・時間帯判定の説明](availability-service/README.md)の「毎時の試験運用」「次の未完了事項」には過去の試験時点の記録が残ります。現在の運用状態はこのREADMEを優先してください。

公式資料：
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
