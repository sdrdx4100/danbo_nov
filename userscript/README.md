# Danbooru → NovelAI userscript

Danbooru の検索結果から **general + character** タグだけを抜き出し、NovelAI (V4.5) で画像を自動生成する Tampermonkey ユーザースクリプトです。スマホ (Kiwi / Firefox + Tampermonkey) で完結します。

`GM_xmlhttpRequest` で Danbooru / NovelAI の両方を叩くので CORS に引っかかりません。トークンは `GM_setValue` に保存し、コードには載せません。

## パイプライン (4段)

1. **検索** — Danbooru `posts.json?tags=<検索ワード> order:random` で投稿リストを取得
2. **タグ抽出** — `tag_string_general` + `tag_string_character` のみ結合（artist / copyright / meta は捨てる）
3. **プロンプト組み立て** — underscore→space 変換・括弧エスケープ・quality サフィックス付与
4. **生成** — `image.novelai.net/ai/generate-image` に POST → 返ってきた **ZIP** を展開して中の PNG を表示

## インストール

1. スマホ/PC のブラウザに **Tampermonkey** を入れる（Kiwi Browser / Firefox なら拡張対応）
2. `danbooru-novelai.user.js` を開く → Tampermonkey が install を提案 → 入れる
   - GitHub の raw URL を開けば自動でインストール画面になります

## 初期設定

Tampermonkey のメニュー（パズルピース → スクリプト名）から:

- **NAIトークンを設定 (pst-***)** — 有料サブスクの Persistent API Token を登録（必須）
- **Danbooru認証を設定** — login / api_key（任意。レート上限が上がる）
- **生成パラメータを編集 (JSON)** — model / 解像度 / steps / scale / sampler / negative / モード等

## 使い方

1. Danbooru で普通にタグ検索する（例: `https://danbooru.donmai.us/posts?tags=1girl`）
2. 右下のパネルでモードを選ぶ
   - **投稿を1件ずつ** — 検索結果を1件ずつ回して生成（`delayMs` 間隔）
   - **タグをシャッフル合成** — 結果全体のタグをシャッフルして N 個で1枚生成
3. **▶ 生成開始**

検索URLに `tags` が無いページでは、実行時に検索ワードを聞きます。

### まずは dry run

**「生成せずタグ抽出のみ (dry run)」** にチェックを入れると、①検索→②タグ抽出だけを実行し、抽出タグと組み立てたプロンプトを `console` とパネルに出します。トークン無しで動くので、まずここが通るのを確認してから ③④ を回すのがおすすめです。

## 注意 / ハマりどころ

- NovelAI は生成ごとに **Anlas** を消費します（基本解像度は無料枠あり）。`iterate` モードで件数を大きくすると一気に消費するので注意。
- レスポンスは画像ではなく **ZIP**。本スクリプトは中央ディレクトリを読んで STORED / DEFLATE 両対応で PNG を展開します（`DecompressionStream` 対応ブラウザが必要）。
- 既定モデルは `nai-diffusion-4-5-full`。Curated を使うなら設定JSONで `nai-diffusion-4-5-curated` に変更。
- 画風 (artist) / キャラ精度 (copyright) を足したくなったら、設定JSONの `qualitySuffix` に手で書き足すか、`extractTags` を拡張してください（今は general + character のみ）。

## 設定キー

| キー | 既定 | 説明 |
|---|---|---|
| `naiEndpoint` | `https://image.novelai.net/ai/generate-image` | 生成エンドポイント |
| `model` | `nai-diffusion-4-5-full` | モデル |
| `width` / `height` | `832` / `1216` | 解像度 |
| `steps` | `28` | ステップ数 |
| `scale` | `5` | CFG scale |
| `sampler` | `k_euler_ancestral` | サンプラー |
| `qualitySuffix` | `best quality, ...` | プロンプト末尾に付与する品質タグ |
| `negative` | `lowres, ...` | ネガティブプロンプト |
| `searchLimit` | `20` | Danbooru取得件数 |
| `mode` | `iterate` | `iterate` / `shuffle` |
| `shuffleCount` | `14` | shuffle時のタグ数 |
| `delayMs` | `2000` | iterate時の生成間隔(ms) |
