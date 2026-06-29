# Danbooru → NovelAI userscript

Danbooru / Gelbooru の検索結果から **general + character** タグだけを「被写体」として抜き出し、**固定したベースの型（絵柄・品質・アーティスト）**に差し込んで NovelAI (V4.5) で画像を自動生成する Tampermonkey ユーザースクリプトです。スマホ (Kiwi / Firefox + Tampermonkey) で完結します。

NovelAI の「チャンク」（ベースプロンプト＋アーティストタグを保存して呼び出す機能）をスクリプト側の **ベースプリセット** として持ち、Danbooru/Gelbooru から拾った被写体タグだけを差し替えて回す、という運用を自動化したものです。

`GM_xmlhttpRequest` で Danbooru / NovelAI の両方を叩くので CORS に引っかかりません。トークンは `GM_setValue` に保存し、コードには載せません。

## 考え方：固定の型 × 可変の被写体

- **固定（型）** = ベースプリセット。絵柄・品質・アーティストなど、毎回同じにしたい部分。`{tags}` が被写体の差し込み位置。
- **可変（被写体）** = Danbooru の `tag_string_character` + `tag_string_general`。投稿ごとに変わる部分。

→ 「同じ絵柄・品質でいろんな被写体を回す」のが速くなります。

## パイプライン (4段)

1. **検索** — Danbooru `posts.json` / Gelbooru `dapi&s=post` で投稿リストを取得（ランダム順）
2. **タグ抽出** — character + general のみ結合（artist / copyright / meta は捨てる。被写体タグのスコープは切替可）
   - **Danbooru**: `tag_string_character` / `tag_string_general` が最初から分かれているのでそのまま
   - **Gelbooru**: posts は `tags` が1本の文字列なので、`s=tag` API で各タグの `type` を引いて character(4) / general(0) に振り分け（artist1 / copyright3 / meta5 は捨てる）
3. **プロンプト組み立て** — 被写体タグを underscore→space 変換・括弧エスケープし、ベースプリセットの `{tags}` 位置に差し込み（無ければ末尾に追記）
4. **生成** — `image.novelai.net/ai/generate-image` に POST → 返ってきた **ZIP** を展開して中の PNG を表示

## インストール

1. スマホ/PC のブラウザに **Tampermonkey** を入れる（Kiwi Browser / Firefox なら拡張対応）
2. `danbooru-novelai.user.js` を開く → Tampermonkey が install を提案 → 入れる
   - GitHub の raw URL を開けば自動でインストール画面になります

## 初期設定

Tampermonkey のメニュー（パズルピース → スクリプト名）から:

- **NAIトークンを設定 (pst-***)** — 有料サブスクの Persistent API Token を登録（必須）
- **Danbooru認証を設定** — login / api_key（任意。レート上限が上がる）
- **Gelbooru認証を設定** — api_key / user_id（Gelbooru を使うなら必須。タグ分類の `s=tag` 呼び出しにも使われる）
- **生成パラメータを編集 (JSON)** — source / model / 解像度 / steps / scale / sampler / negative / モード等

## 使い方

1. Danbooru で普通にタグ検索する（例: `https://danbooru.donmai.us/posts?tags=1girl`）
   - パネルは Danbooru ページ上で動きます。**ソース** を Gelbooru にすると、検索ワードはそのまま Gelbooru API に投げられます
2. **ソース** を選ぶ（Danbooru / Gelbooru）
3. **ベース**（型）を用意する
   - パネル上部の **ベース** ドロップダウンでプリセットを選択。テキストエリアで中身を編集（自動保存）
   - 別の型を保存したいときは **＋新規** で名前を付けて作成、**🗑** で削除
   - 例: `artist:wlop, very aesthetic, best quality, amazing quality, {tags}`
   - `{tags}` の位置に被写体タグが入ります（`{tags}` を書かなければ末尾に追記）
4. **被写体タグ** のスコープを選ぶ（`character + general` / `characterのみ` / `generalのみ`）
5. **モード** を選ぶ
   - **投稿を1件ずつ** — 検索結果を1件ずつ回して生成（`delayMs` 間隔）
   - **タグをシャッフル合成** — 結果全体のタグをシャッフルして N 個で1枚生成
6. **▶ 生成開始**

各結果カードの **📋** でそのプロンプトをコピーできます（NovelAI の UI に手で貼る運用にも対応）。

検索URLに `tags` が無いページでは、実行時に検索ワードを聞きます。

### まずは dry run

**「生成せずタグ抽出のみ (dry run)」** にチェックを入れると、①検索→②タグ抽出だけを実行し、抽出タグと組み立てたプロンプトを `console` とパネルに出します（dry run のカードは破線枠で、プロンプトを最初から展開表示）。トークン無しで動くので、まずここが通るのを確認してから ③④ を回すのがおすすめです。

### 動作の細かい仕様

- **生成開始** を押すたびに前回の結果カードはクリアされます（手動でクリアしたいときは **🗑 クリア**）。
- 実生成（dry run でない）時は、消費枚数を確認するダイアログが出ます（`iterate` なら最大件数ぶん Anlas を消費）。
- **⏹ 停止** は生成中の1枚が終わったところでループを止めます。
- iterate 中はステータスに `生成中: post #1002 (2/3)` のように進捗が出ます。

## 注意 / ハマりどころ

- NovelAI は生成ごとに **Anlas** を消費します（基本解像度は無料枠あり）。`iterate` モードで件数を大きくすると一気に消費するので注意。
- レスポンスは画像ではなく **ZIP**。本スクリプトは中央ディレクトリを読んで STORED / DEFLATE 両対応で PNG を展開します（`DecompressionStream` 対応ブラウザが必要）。
- 既定モデルは `nai-diffusion-4-5-full`。Curated を使うなら設定JSONで `nai-diffusion-4-5-curated` に変更。
- 画風 (artist) / 品質を固定したいときは、**ベースプリセット** にそのまま書きます（`artist:xxx, best quality, ... , {tags}`）。被写体だけ Danbooru から差し替わります。
- ベースプリセットは `GM_setValue('base_presets', {...})` に保存されます。
- **Gelbooru** は API に `api_key` + `user_id` が必須です（未設定だとタグ分類の `s=tag` が効かず、メタ語を簡易ブラックリストで間引いた素のタグを general として使うフォールバックになります）。投稿1ページ分のユニークタグをまとめて分類するので、`s=tag` 呼び出しは検索1回につき数回程度です。

## 設定キー

| キー | 既定 | 説明 |
|---|---|---|
| `source` | `danbooru` | 取得元 `danbooru` / `gelbooru` |
| `naiEndpoint` | `https://image.novelai.net/ai/generate-image` | 生成エンドポイント |
| `model` | `nai-diffusion-4-5-full` | モデル |
| `width` / `height` | `832` / `1216` | 解像度 |
| `steps` | `28` | ステップ数 |
| `scale` | `5` | CFG scale |
| `sampler` | `k_euler_ancestral` | サンプラー |
| `negative` | `lowres, ...` | ネガティブプロンプト |
| `searchLimit` | `20` | Danbooru取得件数 |
| `mode` | `iterate` | `iterate` / `shuffle` |
| `shuffleCount` | `14` | shuffle時のタグ数 |
| `delayMs` | `2000` | iterate時の生成間隔(ms) |
| `tagScope` | `both` | 被写体タグ範囲 `both` / `character` / `general` |

※ ベースプリセット（型）は設定JSONとは別に **パネル上部のベース欄** で管理します。
