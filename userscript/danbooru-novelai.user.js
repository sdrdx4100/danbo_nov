// ==UserScript==
// @name         Danbooru → NovelAI 生成
// @namespace    https://github.com/sdrdx4100/danbo_nov
// @version      0.1.0
// @description  Danbooruの検索結果から general + character タグを抽出し、NovelAI(V4.5)で画像を自動生成する。GM_xmlhttpRequestでCORSを回避し、トークンはGM_setValueに保存（リポジトリには載せない）。
// @author       sdrdx4100
// @match        https://danbooru.donmai.us/*
// @connect      danbooru.donmai.us
// @connect      gelbooru.com
// @connect      image.novelai.net
// @connect      novelai.net
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        GM_addStyle
// @grant        GM_setClipboard
// @run-at       document-idle
// @noframes
// ==/UserScript==

/*
 * パイプライン（4段）:
 *   ① 検索ワード → Danbooru posts.json で投稿リスト取得
 *   ② 各投稿の tag_string_general + tag_string_character だけ抽出
 *   ③ NovelAI 用プロンプトに組み立て
 *   ④ image.novelai.net に投げ、返ってきた ZIP の中の PNG を展開して表示
 *
 * 「生成せずタグ抽出のみ (dry run)」をONにすると ①② だけ動かして console に出します。
 * まずここが動くのを確認してから token を入れて ③④ を回すのがおすすめ。
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // 設定 (GM_setValue に保存。コードにベタ書きしない)
  // ---------------------------------------------------------------------------
  const DEFAULTS = {
    source: 'danbooru', // 'danbooru' / 'gelbooru'
    naiEndpoint: 'https://image.novelai.net/ai/generate-image',
    model: 'nai-diffusion-4-5-full', // curated を使うなら 'nai-diffusion-4-5-curated'
    width: 832,
    height: 1216,
    steps: 28,
    scale: 5,
    sampler: 'k_euler_ancestral',
    negative:
      'lowres, worst quality, low quality, bad anatomy, bad hands, jpeg artifacts, '
      + 'signature, watermark, username, blurry, text, error, extra digits',
    searchLimit: 20, // Danbooruから取得する投稿数
    mode: 'iterate', // 'iterate' = 投稿を1件ずつ / 'shuffle' = 全タグをシャッフル合成
    shuffleCount: 14, // shuffle時に使うタグ数
    delayMs: 2000, // iterate時の生成間隔(ms) ※Anlas消費に注意
    tagScope: 'both', // 'both' = character+general / 'character' / 'general'
  };

  // 設定キー一覧（JSONエディタで編集できるもの）
  const EDITABLE_KEYS = Object.keys(DEFAULTS);

  const cfg = (key) => {
    const v = GM_getValue('cfg_' + key, DEFAULTS[key]);
    return v;
  };
  const setCfg = (key, val) => GM_setValue('cfg_' + key, val);

  const getToken = () => GM_getValue('nai_token', '');
  const setToken = (t) => GM_setValue('nai_token', t);

  // ---------------------------------------------------------------------------
  // ベースプリセット (NovelAIの「チャンク」相当)
  //   絵柄・品質・アーティストなどの「固定の型」を名前付きで保存しておき、
  //   Danbooruから取った被写体タグ(character+general)を {tags} 位置に差し込む。
  //   {tags} が無ければ末尾に追記する。
  // ---------------------------------------------------------------------------
  const DEFAULT_PRESETS = {
    'デフォルト': 'very aesthetic, best quality, amazing quality, absurdres, {tags}',
  };

  const getPresets = () => GM_getValue('base_presets', DEFAULT_PRESETS);
  const setPresets = (p) => GM_setValue('base_presets', p);

  function getActivePresetName() {
    const presets = getPresets();
    const names = Object.keys(presets);
    let name = GM_getValue('active_base', names[0]);
    if (!(name in presets)) name = names[0];
    return name;
  }
  const setActivePresetName = (name) => GM_setValue('active_base', name);
  const getBaseText = () => getPresets()[getActivePresetName()] || '{tags}';

  // ---------------------------------------------------------------------------
  // 汎用ヘルパ
  // ---------------------------------------------------------------------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const shuffle = (arr) => {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  };

  // GM_xmlhttpRequest を Promise 化（CORS回避の要）
  function gmRequest(opts) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: opts.method || 'GET',
        url: opts.url,
        headers: opts.headers || {},
        data: opts.data,
        responseType: opts.responseType,
        timeout: opts.timeout || 120000,
        onload: (r) => {
          if (r.status >= 200 && r.status < 300) {
            resolve(r);
          } else {
            const body = (r.responseText || '').slice(0, 300);
            reject(new Error(`HTTP ${r.status} ${r.statusText || ''} ${body}`));
          }
        },
        onerror: () => reject(new Error('ネットワークエラー')),
        ontimeout: () => reject(new Error('タイムアウト')),
      });
    });
  }

  // ---------------------------------------------------------------------------
  // ① + ② 取得 & タグ抽出（ソース抽象化）
  //    各ソースは投稿を {id, character:[], general:[]} に正規化して返す。
  //    被写体タグだけを取り出す（artist/copyright/meta は捨てる）。
  //    絵柄/品質/アーティストはベースプリセット側で固定する。
  // ---------------------------------------------------------------------------
  const splitTags = (s) => (s || '').split(/\s+/).filter(Boolean);

  // Danbooru: posts.json が最初からカテゴリ別フィールドを返すので分類は不要
  async function fetchDanbooru(searchTags, limit) {
    const query = /\border:/.test(searchTags) ? searchTags : `${searchTags} order:random`;
    let url =
      'https://danbooru.donmai.us/posts.json?tags=' +
      encodeURIComponent(query) + '&limit=' + encodeURIComponent(limit);
    const login = GM_getValue('danbooru_login', '');
    const apiKey = GM_getValue('danbooru_api_key', '');
    if (login && apiKey) {
      url += '&login=' + encodeURIComponent(login) + '&api_key=' + encodeURIComponent(apiKey);
    }
    const r = await gmRequest({ url, headers: { Accept: 'application/json' } });
    const data = JSON.parse(r.responseText);
    const posts = Array.isArray(data) ? data : [];
    return posts.map((p) => ({
      id: p.id,
      character: splitTags(p.tag_string_character),
      general: splitTags(p.tag_string_general),
    }));
  }

  // Gelbooru: posts は tags が1本の文字列。s=tag API で type を引いて分類する。
  //   type: 0=general, 1=artist, 3=copyright, 4=character, 5=metadata
  const GELBOORU_META_FALLBACK = new Set([
    'highres', 'absurdres', 'lowres', 'commentary', 'commentary_request',
    'translated', 'translation_request', 'bad_id', 'bad_pixiv_id', 'tagme',
  ]);

  function gelbooruCreds() {
    const apiKey = GM_getValue('gelbooru_api_key', '');
    const userId = GM_getValue('gelbooru_user_id', '');
    return apiKey && userId
      ? `&api_key=${encodeURIComponent(apiKey)}&user_id=${encodeURIComponent(userId)}`
      : '';
  }

  async function gelbooruClassify(tagNames) {
    // names= に空白区切りでまとめて問い合わせ（長すぎ防止に分割）
    const map = new Map();
    const creds = gelbooruCreds();
    const CHUNK = 80;
    for (let i = 0; i < tagNames.length; i += CHUNK) {
      const chunk = tagNames.slice(i, i + CHUNK);
      const url =
        'https://gelbooru.com/index.php?page=dapi&s=tag&q=index&json=1&limit=' +
        chunk.length + '&names=' + encodeURIComponent(chunk.join(' ')) + creds;
      try {
        const r = await gmRequest({ url, headers: { Accept: 'application/json' } });
        const data = JSON.parse(r.responseText);
        const tags = Array.isArray(data) ? data : data.tag || [];
        tags.forEach((t) => map.set(t.name, Number(t.type)));
      } catch (e) {
        console.warn('[D→NAI] Gelbooru tag分類に失敗:', e.message);
      }
    }
    return map;
  }

  async function fetchGelbooru(searchTags, limit) {
    const query = /\bsort:/.test(searchTags) ? searchTags : `${searchTags} sort:random`;
    const url =
      'https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit=' +
      encodeURIComponent(limit) + '&tags=' + encodeURIComponent(query) + gelbooruCreds();
    const r = await gmRequest({ url, headers: { Accept: 'application/json' } });
    const data = JSON.parse(r.responseText);
    const posts = Array.isArray(data) ? data : data.post || [];

    // 全投稿のユニークタグを集めて1回（〜数回）で分類
    const uniq = [...new Set(posts.flatMap((p) => splitTags(p.tags)))];
    const typeMap = await gelbooruClassify(uniq);

    return posts.map((p) => {
      const all = splitTags(p.tags);
      const character = [];
      const general = [];
      let classified = false;
      all.forEach((t) => {
        const ty = typeMap.get(t);
        if (ty === undefined) return; // 未分類は後でフォールバック判定
        classified = true;
        if (ty === 4) character.push(t);
        else if (ty === 0) general.push(t);
        // 1=artist / 3=copyright / 5=meta は捨てる
      });
      // 分類が全く効かなかった場合（認証なし等）は素のタグ-メタ で代用
      if (!classified) {
        return {
          id: p.id,
          character: [],
          general: all.filter((t) => !GELBOORU_META_FALLBACK.has(t)),
        };
      }
      return { id: p.id, character, general };
    });
  }

  const SOURCES = {
    danbooru: { label: 'Danbooru', fetch: fetchDanbooru },
    gelbooru: { label: 'Gelbooru', fetch: fetchGelbooru },
  };

  function fetchSubjects(searchTags, limit) {
    const src = SOURCES[cfg('source')] || SOURCES.danbooru;
    return src.fetch(searchTags, limit);
  }

  // tagScope に応じて被写体タグを選ぶ（item = {character, general}）
  function scopedTags(item) {
    switch (cfg('tagScope')) {
      case 'character':
        return item.character;
      case 'general':
        return item.general;
      default:
        return [...item.character, ...item.general];
    }
  }

  // ---------------------------------------------------------------------------
  // ③ NovelAI プロンプト組み立て
  //    固定ベース(型) に 被写体タグ を {tags} 位置で差し込む。なければ末尾に追記。
  // ---------------------------------------------------------------------------
  function tagToNai(t) {
    // Danbooru形式 underscore → space、括弧はNAIの記法と衝突するのでエスケープ
    return t.replace(/_/g, ' ').replace(/([()])/g, '\\$1');
  }

  function buildPrompt(tags) {
    const body = tags.map(tagToNai).join(', ');
    const base = (getBaseText() || '').trim();
    if (!base) return body;
    if (base.includes('{tags}')) return base.replace(/\{tags\}/g, body);
    return body ? `${base}, ${body}` : base;
  }

  function buildPayload(positive, negative, seed) {
    return {
      input: positive,
      model: cfg('model'),
      action: 'generate',
      parameters: {
        params_version: 3,
        width: Number(cfg('width')),
        height: Number(cfg('height')),
        scale: Number(cfg('scale')),
        sampler: cfg('sampler'),
        steps: Number(cfg('steps')),
        n_samples: 1,
        ucPreset: 0,
        qualityToggle: true,
        autoSmea: false,
        dynamic_thresholding: false,
        controlnet_strength: 1,
        legacy: false,
        add_original_image: true,
        cfg_rescale: 0,
        noise_schedule: 'karras',
        legacy_v3_extend: false,
        skip_cfg_above_sigma: null,
        seed: seed,
        negative_prompt: negative,
        // V4.5: キャラごとの character_prompts を入れる枠。今は base にまとめている。
        characterPrompts: [],
        v4_prompt: {
          caption: { base_caption: positive, char_captions: [] },
          use_coords: false,
          use_order: true,
        },
        v4_negative_prompt: {
          caption: { base_caption: negative, char_captions: [] },
          use_coords: false,
          use_order: false,
        },
      },
    };
  }

  // ---------------------------------------------------------------------------
  // ④ NovelAI 生成 → ZIP(arraybuffer) を取得
  // ---------------------------------------------------------------------------
  async function naiGenerate(positive) {
    const token = getToken();
    if (!token) {
      throw new Error('NAIトークン未設定（メニュー「NAIトークンを設定」から登録してください）');
    }
    const seed = Math.floor(Math.random() * 2 ** 32);
    const payload = buildPayload(positive, cfg('negative'), seed);
    const r = await gmRequest({
      method: 'POST',
      url: cfg('naiEndpoint'),
      headers: {
        Authorization: 'Bearer ' + token,
        'Content-Type': 'application/json',
        Accept: 'application/x-zip-compressed',
      },
      data: JSON.stringify(payload),
      responseType: 'arraybuffer',
      timeout: 180000,
    });
    return r.response; // ArrayBuffer (ZIP)
  }

  // ④-b ZIPから最初のPNGを展開（STORED/ DEFLATE 両対応）
  async function inflateRaw(bytes) {
    if (typeof DecompressionStream === 'undefined') {
      throw new Error('このブラウザは DecompressionStream 非対応です（deflate展開不可）');
    }
    const ds = new DecompressionStream('deflate-raw');
    const stream = new Response(bytes).body.pipeThrough(ds);
    const ab = await new Response(stream).arrayBuffer();
    return new Uint8Array(ab);
  }

  async function extractFirstPng(arrayBuffer) {
    const dv = new DataView(arrayBuffer);
    const u8 = new Uint8Array(arrayBuffer);
    const len = arrayBuffer.byteLength;

    // End of Central Directory (EOCD) を末尾から探索
    const SIG_EOCD = 0x06054b50;
    let eocd = -1;
    for (let i = len - 22; i >= 0; i--) {
      if (dv.getUint32(i, true) === SIG_EOCD) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) throw new Error('ZIPのEOCDが見つかりません');

    const cdCount = dv.getUint16(eocd + 10, true);
    let ptr = dv.getUint32(eocd + 16, true); // central directory offset
    const td = new TextDecoder();

    for (let i = 0; i < cdCount; i++) {
      if (dv.getUint32(ptr, true) !== 0x02014b50) {
        throw new Error('中央ディレクトリの署名が不正です');
      }
      const method = dv.getUint16(ptr + 10, true);
      const compSize = dv.getUint32(ptr + 20, true);
      const fnLen = dv.getUint16(ptr + 28, true);
      const extraLen = dv.getUint16(ptr + 30, true);
      const commentLen = dv.getUint16(ptr + 32, true);
      const localOff = dv.getUint32(ptr + 42, true);
      const name = td.decode(u8.subarray(ptr + 46, ptr + 46 + fnLen));
      ptr += 46 + fnLen + extraLen + commentLen;

      if (!name.toLowerCase().endsWith('.png')) continue;

      // ローカルヘッダから実データ位置を割り出す
      const lfnLen = dv.getUint16(localOff + 26, true);
      const lextraLen = dv.getUint16(localOff + 28, true);
      const dataStart = localOff + 30 + lfnLen + lextraLen;
      const comp = u8.subarray(dataStart, dataStart + compSize);

      if (method === 0) return comp.slice(); // STORED
      if (method === 8) return await inflateRaw(comp); // DEFLATE
      throw new Error('未対応の圧縮方式: ' + method);
    }
    throw new Error('ZIP内にPNGがありません');
  }

  // ---------------------------------------------------------------------------
  // 検索ワードの取得（現在のDanbooru検索URLから / 無ければ入力を促す）
  // ---------------------------------------------------------------------------
  function getSearchTags() {
    const t = new URL(location.href).searchParams.get('tags') || '';
    return t.trim();
  }

  // ---------------------------------------------------------------------------
  // UI
  // ---------------------------------------------------------------------------
  GM_addStyle(`
    #dnai-panel {
      position: fixed; right: 12px; bottom: 12px; z-index: 99999;
      width: min(360px, 92vw); max-height: 80vh; display: flex; flex-direction: column;
      background: #1f2430; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 10px;
      font: 13px/1.45 system-ui, sans-serif; box-shadow: 0 8px 24px rgba(0,0,0,.4);
    }
    #dnai-head {
      display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer;
      background: #2a3140; border-radius: 10px 10px 0 0; user-select: none;
    }
    #dnai-head b { flex: 1; font-size: 13px; }
    #dnai-body { padding: 10px; overflow: auto; }
    #dnai-panel.dnai-collapsed #dnai-body { display: none; }
    #dnai-panel .row { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
    #dnai-panel label { display: flex; align-items: center; gap: 4px; }
    #dnai-panel select, #dnai-panel input[type=number] {
      background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 6px; padding: 3px 6px;
    }
    #dnai-panel input[type=number] { width: 64px; }
    #dnai-panel select#dnai-base { flex: 1; min-width: 0; }
    #dnai-panel textarea {
      width: 100%; box-sizing: border-box; resize: vertical; min-height: 48px;
      background: #11151c; color: #e6e6e6; border: 1px solid #3a4150; border-radius: 6px;
      padding: 5px 7px; font: 12px/1.4 ui-monospace, monospace;
    }
    #dnai-panel .hint { font-size: 11px; color: #6b7280; margin: 2px 0 6px; }
    #dnai-panel button {
      background: #3b82f6; color: #fff; border: 0; border-radius: 6px; padding: 6px 10px;
      cursor: pointer; font-size: 13px;
    }
    #dnai-panel button.sec { background: #4b5563; }
    #dnai-panel button.mini { padding: 3px 8px; font-size: 12px; }
    #dnai-panel button:disabled { opacity: .5; cursor: default; }
    #dnai-status { margin: 6px 0; min-height: 18px; color: #9ca3af; word-break: break-all; }
    #dnai-results { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    #dnai-results .card { background: #11151c; border: 1px solid #2a3140; border-radius: 8px; padding: 6px; }
    #dnai-results .card.dry { border-style: dashed; border-color: #4b5563; }
    #dnai-results img { width: 100%; border-radius: 4px; display: block; }
    #dnai-results details { margin-top: 4px; }
    #dnai-results summary { cursor: pointer; color: #93c5fd; }
    #dnai-results .prompt { font-size: 11px; color: #cbd5e1; word-break: break-word; white-space: pre-wrap; }
    #dnai-results .label { font-size: 11px; color: #9ca3af; margin-top: 2px; display: flex; align-items: center; gap: 6px; }
    #dnai-results .label .copy {
      margin-left: auto; background: #2a3140; color: #93c5fd; border: 0; border-radius: 4px;
      padding: 1px 6px; font-size: 11px; cursor: pointer;
    }
  `);

  let running = false;

  function el(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  const panel = el(`
    <div id="dnai-panel">
      <div id="dnai-head"><b>🎨 Danbooru → NovelAI</b><span id="dnai-toggle">▾</span></div>
      <div id="dnai-body">
        <div class="row">
          <label>ソース
            <select id="dnai-source">
              <option value="danbooru">Danbooru</option>
              <option value="gelbooru">Gelbooru</option>
            </select>
          </label>
        </div>
        <div class="row">
          <label>ベース<select id="dnai-base"></select></label>
          <button id="dnai-base-new" class="sec mini">＋新規</button>
          <button id="dnai-base-del" class="sec mini">🗑</button>
        </div>
        <textarea id="dnai-base-text" spellcheck="false"></textarea>
        <div class="hint">固定の型(絵柄・品質・artist等)。被写体タグは <code>{tags}</code> 位置に差し込み（無ければ末尾に追記）</div>
        <div class="row">
          <label>モード
            <select id="dnai-mode">
              <option value="iterate">投稿を1件ずつ</option>
              <option value="shuffle">タグをシャッフル合成</option>
            </select>
          </label>
          <label>件数<input type="number" id="dnai-limit" min="1" max="100"></label>
        </div>
        <div class="row">
          <label>被写体タグ
            <select id="dnai-scope">
              <option value="both">character + general</option>
              <option value="character">characterのみ</option>
              <option value="general">generalのみ</option>
            </select>
          </label>
        </div>
        <div class="row">
          <label><input type="checkbox" id="dnai-dry"> 生成せずタグ抽出のみ (dry run)</label>
        </div>
        <div class="row">
          <button id="dnai-run">▶ 生成開始</button>
          <button id="dnai-stop" class="sec" disabled>⏹ 停止</button>
          <button id="dnai-clear" class="sec">🗑 クリア</button>
          <button id="dnai-settings" class="sec">⚙ 設定</button>
        </div>
        <div id="dnai-status"></div>
        <div id="dnai-results"></div>
      </div>
    </div>
  `);

  const $ = (sel) => panel.querySelector(sel);
  const setStatus = (msg) => {
    $('#dnai-status').textContent = msg;
  };

  const objectUrls = [];

  function addResult(imgUrl, prompt, label, opts = {}) {
    const card = el(`<div class="card"></div>`);
    if (opts.dry) card.classList.add('dry');
    if (imgUrl) {
      objectUrls.push(imgUrl);
      const a = el(`<a download="novelai.png"></a>`);
      a.href = imgUrl;
      const img = el(`<img>`);
      img.src = imgUrl;
      a.appendChild(img);
      card.appendChild(a);
    }
    const lab = el(`<div class="label"></div>`);
    lab.append(label || '');
    const copyBtn = el(`<button class="copy" title="プロンプトをコピー">📋</button>`);
    copyBtn.addEventListener('click', () => {
      const done = () => { copyBtn.textContent = '✓'; setTimeout(() => (copyBtn.textContent = '📋'), 1000); };
      if (typeof GM_setClipboard === 'function') {
        GM_setClipboard(prompt, 'text');
        done();
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(prompt).then(done, () => (copyBtn.textContent = '×'));
      }
    });
    lab.appendChild(copyBtn);
    card.appendChild(lab);
    // dry runはタグを見るのが目的なので最初から開いておく
    const det = el(
      `<details${opts.dry ? ' open' : ''}><summary>prompt</summary><div class="prompt"></div></details>`
    );
    det.querySelector('.prompt').textContent = prompt;
    card.appendChild(det);
    $('#dnai-results').prepend(card);
  }

  function clearResults() {
    objectUrls.splice(0).forEach((u) => URL.revokeObjectURL(u));
    $('#dnai-results').textContent = '';
  }

  function setRunning(state) {
    running = state;
    $('#dnai-run').disabled = state;
    $('#dnai-stop').disabled = !state;
  }

  async function generateAndShow(tags, label) {
    const prompt = buildPrompt(tags);
    console.log('[D→NAI]', label, '\n  tags:', tags, '\n  prompt:', prompt);

    if ($('#dnai-dry').checked) {
      addResult(null, prompt, `${label} (dry run)`, { dry: true });
      return;
    }

    const zip = await naiGenerate(prompt);
    const png = await extractFirstPng(zip);
    const url = URL.createObjectURL(new Blob([png], { type: 'image/png' }));
    addResult(url, prompt, label);
  }

  async function run() {
    if (running) return;
    const dry = $('#dnai-dry').checked;
    setRunning(true);
    clearResults();
    try {
      const srcLabel = (SOURCES[cfg('source')] || SOURCES.danbooru).label;
      let searchTags = getSearchTags();
      if (!searchTags) {
        searchTags = (prompt(`${srcLabel}検索ワード（例: 1girl）`) || '').trim();
        if (!searchTags) {
          setStatus('検索ワードがありません');
          return;
        }
      }

      setStatus(`${srcLabel}検索中: ${searchTags} ...`);
      const posts = await fetchSubjects(searchTags, Number(cfg('searchLimit')));
      if (!posts.length) {
        setStatus('投稿が見つかりませんでした');
        return;
      }
      console.log(`[D→NAI] ${srcLabel} ${posts.length}件取得`);

      if (cfg('mode') === 'shuffle') {
        if (!dry && !confirm('NovelAIで1枚生成します（Anlasを消費）。続行しますか？')) {
          setStatus('キャンセルしました');
          return;
        }
        const pool = new Map();
        posts.forEach((p) =>
          scopedTags(p).forEach((t) => pool.set(t, (pool.get(t) || 0) + 1))
        );
        const tags = shuffle([...pool.keys()]).slice(0, Number(cfg('shuffleCount')));
        if (!dry) setStatus('生成中: shuffle ...');
        await generateAndShow(tags, 'shuffle');
      } else {
        // iterateで生成する投稿（タグありのみ）を先に確定させてからAnlas確認
        const targets = posts.filter((p) => scopedTags(p).length);
        if (!dry && targets.length) {
          if (!confirm(`NovelAIで最大${targets.length}枚生成します（${targets.length}回Anlas消費）。続行しますか？`)) {
            setStatus('キャンセルしました');
            return;
          }
        }
        let done = 0;
        for (const post of targets) {
          if (!running) break;
          const label = `post #${post.id} (${done + 1}/${targets.length})`;
          if (!dry) setStatus(`生成中: ${label} ...`);
          await generateAndShow(scopedTags(post), label);
          done++;
          if (!running) break;
          if (!dry) await sleep(Number(cfg('delayMs')));
        }
      }
      setStatus(running ? '完了' : '停止しました');
    } catch (e) {
      console.error('[D→NAI]', e);
      setStatus('エラー: ' + e.message);
    } finally {
      setRunning(false);
    }
  }

  function openSettingsEditor() {
    const current = {};
    EDITABLE_KEYS.forEach((k) => (current[k] = cfg(k)));
    const input = prompt(
      '設定をJSONで編集（保存する場合はOK）:',
      JSON.stringify(current, null, 2)
    );
    if (input == null) return;
    try {
      const parsed = JSON.parse(input);
      EDITABLE_KEYS.forEach((k) => {
        if (k in parsed) setCfg(k, parsed[k]);
      });
      syncControls();
      setStatus('設定を保存しました');
    } catch (e) {
      alert('JSONの解析に失敗: ' + e.message);
    }
  }

  function syncControls() {
    $('#dnai-source').value = cfg('source');
    $('#dnai-mode').value = cfg('mode');
    $('#dnai-limit').value = cfg('searchLimit');
    $('#dnai-scope').value = cfg('tagScope');
    syncBaseControls();
  }

  // ベースプリセットのドロップダウン＋テキストエリアを現在状態に同期
  function syncBaseControls() {
    const presets = getPresets();
    const active = getActivePresetName();
    const sel = $('#dnai-base');
    sel.textContent = '';
    Object.keys(presets).forEach((name) => {
      const o = el(`<option></option>`);
      o.value = name;
      o.textContent = name;
      sel.appendChild(o);
    });
    sel.value = active;
    $('#dnai-base-text').value = presets[active] || '';
  }

  // テキストエリアの中身を現在のプリセットに保存
  function saveBaseText() {
    const presets = getPresets();
    presets[getActivePresetName()] = $('#dnai-base-text').value;
    setPresets(presets);
  }

  // イベント
  $('#dnai-head').addEventListener('click', () => {
    panel.classList.toggle('dnai-collapsed');
    $('#dnai-toggle').textContent = panel.classList.contains('dnai-collapsed') ? '▸' : '▾';
  });
  $('#dnai-run').addEventListener('click', run);
  $('#dnai-stop').addEventListener('click', () => {
    setStatus('停止します（生成中の1枚を待っています）...');
    running = false;
  });
  $('#dnai-clear').addEventListener('click', () => {
    clearResults();
    setStatus('');
  });
  $('#dnai-settings').addEventListener('click', openSettingsEditor);
  $('#dnai-source').addEventListener('change', (e) => setCfg('source', e.target.value));
  $('#dnai-mode').addEventListener('change', (e) => setCfg('mode', e.target.value));
  $('#dnai-scope').addEventListener('change', (e) => setCfg('tagScope', e.target.value));
  $('#dnai-limit').addEventListener('change', (e) =>
    setCfg('searchLimit', Number(e.target.value) || DEFAULTS.searchLimit)
  );

  // ベースプリセット
  $('#dnai-base').addEventListener('change', (e) => {
    setActivePresetName(e.target.value);
    $('#dnai-base-text').value = getPresets()[e.target.value] || '';
  });
  $('#dnai-base-text').addEventListener('change', saveBaseText);
  $('#dnai-base-new').addEventListener('click', () => {
    const name = (prompt('新しいベースプリセット名') || '').trim();
    if (!name) return;
    const presets = getPresets();
    if (!(name in presets)) presets[name] = '{tags}';
    setPresets(presets);
    setActivePresetName(name);
    syncBaseControls();
  });
  $('#dnai-base-del').addEventListener('click', () => {
    const presets = getPresets();
    const names = Object.keys(presets);
    if (names.length <= 1) {
      alert('最後の1件は削除できません');
      return;
    }
    const active = getActivePresetName();
    if (!confirm(`ベース「${active}」を削除しますか？`)) return;
    delete presets[active];
    setPresets(presets);
    setActivePresetName(Object.keys(presets)[0]);
    syncBaseControls();
  });

  document.body.appendChild(panel);
  syncControls();

  // メニューコマンド
  GM_registerMenuCommand('NAIトークンを設定 (pst-***)', () => {
    const t = prompt('NovelAI Persistent API Token (pst-...)', getToken());
    if (t != null) {
      setToken(t.trim());
      setStatus('トークンを保存しました');
    }
  });
  GM_registerMenuCommand('Danbooru認証を設定 (login / api_key)', () => {
    const login = prompt('Danbooru login (空でOK)', GM_getValue('danbooru_login', ''));
    if (login == null) return;
    const key = prompt('Danbooru api_key (空でOK)', GM_getValue('danbooru_api_key', ''));
    if (key == null) return;
    GM_setValue('danbooru_login', login.trim());
    GM_setValue('danbooru_api_key', key.trim());
    setStatus('Danbooru認証を保存しました');
  });
  GM_registerMenuCommand('Gelbooru認証を設定 (api_key / user_id)', () => {
    const key = prompt('Gelbooru api_key', GM_getValue('gelbooru_api_key', ''));
    if (key == null) return;
    const uid = prompt('Gelbooru user_id', GM_getValue('gelbooru_user_id', ''));
    if (uid == null) return;
    GM_setValue('gelbooru_api_key', key.trim());
    GM_setValue('gelbooru_user_id', uid.trim());
    setStatus('Gelbooru認証を保存しました');
  });
  GM_registerMenuCommand('生成パラメータを編集 (JSON)', openSettingsEditor);
})();
