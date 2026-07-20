// ربات مدیریت اطلاع‌رسانی خاموشی برق — روی Cloudflare Workers
//
// مسیرها:
//   POST /api/pending  <- reader.py (گیت‌هاب) پیام جدید رو اینجا ثبت می‌کنه
//   POST /api/groups   <- reader.py گروه تازه‌فعال‌شده رو اینجا ثبت می‌کنه
//   POST /webhook      <- وب‌هوک تلگرام برای ربات مدیریت (دکمه‌ها و دستورات)
// scheduled()          <- Cron Trigger؛ هر چند دقیقه تایم‌اوت‌ها رو چک می‌کنه

import nacl from "tweetnacl";
import sealedbox from "tweetnacl-sealedbox-js";

// فقط همین سکرت‌های کم‌ریسک از طریق تلگرام قابل تغییرن — عمداً محدود شده،
// چیزی مثل TELEGRAM_SESSION هرگز نباید از این مسیر عوض بشه.
const EDITABLE_SECRETS = ["STATUS_QUERY_TIMES", "ACTIVATION_CODE"];

const OK = (body = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const ERR = (msg, status = 400) =>
  new Response(JSON.stringify({ error: msg }), { status, headers: { "content-type": "application/json" } });

const DEFAULT_SETTINGS = { autosend: false, timeout_minutes: 15, default_group_ids: null };

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.MANAGER_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

function sendToAdmin(env, text, reply_markup) {
  return tg(env, "sendMessage", { chat_id: env.ADMIN_USER_ID, text, parse_mode: "HTML", reply_markup });
}

async function getJSON(env, key, fallback) {
  const v = await env.BARGH_KV.get(key, "json");
  return v === null ? fallback : v;
}

const setJSON = (env, key, value) => env.BARGH_KV.put(key, JSON.stringify(value));

function groupsText(groups) {
  const entries = Object.entries(groups);
  if (!entries.length) return "📋 <b>گروه‌ها</b>\n\nهنوز هیچ گروهی ثبت نشده.";
  const lines = entries.map(([id, title], i) => `${i + 1}. <b>${title}</b>\n    <code>${id}</code>`);
  return "📋 <b>گروه‌های ثبت‌شده</b>\n\n" + lines.join("\n\n");
}

function statusText(groups, settings, pendingCount) {
  const targets = resolveTargets(groups, settings);
  return (
    `📊 <b>وضعیت فعلی</b>\n\n` +
    `${settings.autosend ? "🟢" : "🔴"} ارسال خودکار: <b>${settings.autosend ? "روشن" : "خاموش"}</b>\n` +
    `⏱ زمان انتظار: <b>${settings.timeout_minutes || 15}</b> دقیقه\n` +
    `📍 گروه‌های مقصد: <b>${targets.length}</b> از ${Object.keys(groups).length}\n` +
    `📨 پیام‌های در انتظار: <b>${pendingCount}</b>`
  );
}

function historyText(history) {
  if (!history.length) return "🕘 <b>تاریخچه</b>\n\nهنوز پیامی ارسال نشده.";
  const lines = history
    .slice(-10)
    .reverse()
    .map((h) => {
      const d = new Date(h.sent_at);
      const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      const mode = h.mode === "auto-timeout" ? "⏱ خودکار" : "✅ دستی";
      const clean = h.text.replace(/<[^>]+>/g, "").replace(/\n/g, " ").slice(0, 60);
      return `${mode} — ${time}\n${clean}…`;
    });
  return "🕘 <b>۱۰ پیام آخر</b>\n\n" + lines.join("\n\n");
}

function mainMenuKeyboard(settings) {
  return {
    inline_keyboard: [
      [
        { text: "📋 گروه‌ها", callback_data: "menu:groups" },
        { text: "📊 وضعیت", callback_data: "menu:status" },
      ],
      [
        {
          text: settings.autosend ? "🔴 خاموش کردن ارسال خودکار" : "🟢 روشن کردن ارسال خودکار",
          callback_data: "menu:autosend_toggle",
        },
      ],
      [
        { text: "⏱ زمان انتظار", callback_data: "menu:timeout" },
        { text: "🕘 تاریخچه", callback_data: "menu:history" },
      ],
    ],
  };
}

function timeoutPickerKeyboard() {
  return {
    inline_keyboard: [
      [
        { text: "۵", callback_data: "timeout:5" },
        { text: "۱۰", callback_data: "timeout:10" },
        { text: "۱۵", callback_data: "timeout:15" },
      ],
      [
        { text: "۳۰", callback_data: "timeout:30" },
        { text: "۶۰", callback_data: "timeout:60" },
      ],
      [{ text: "‹ بازگشت", callback_data: "menu:main" }],
    ],
  };
}

const BACK_KEYBOARD = { inline_keyboard: [[{ text: "‹ بازگشت", callback_data: "menu:main" }]] };

function resolveTargets(groups, settings) {
  const def = settings.default_group_ids;
  if (def && def.length) return def.filter((id) => id in groups);
  return Object.keys(groups);
}

async function githubApi(env, method, path, body) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "bargh-manager-worker",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`GitHub API error ${r.status}: ${JSON.stringify(data)}`);
  return data;
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

async function setGithubSecret(env, name, value) {
  const keyInfo = await githubApi(env, "GET", "/actions/secrets/public-key");
  const publicKey = base64ToBytes(keyInfo.key);
  const messageBytes = new TextEncoder().encode(value);
  const encryptedBytes = sealedbox.seal(messageBytes, publicKey);
  await githubApi(env, "PUT", `/actions/secrets/${name}`, {
    encrypted_value: bytesToBase64(encryptedBytes),
    key_id: keyInfo.key_id,
  });
}

async function sendToGroups(env, text, targets) {
  const results = [];
  for (const chatId of targets) {
    const r = await tg(env, "sendMessage", { chat_id: chatId, text, parse_mode: "HTML" });
    results.push({ chatId, ok: !!r.ok, description: r.description });
  }
  return results;
}

async function pushHistory(env, entry) {
  const history = await getJSON(env, "history", []);
  history.push(entry);
  await setJSON(env, "history", history.slice(-50));
}

async function createPendingPreview(env, pid, entry) {
  const groups = await getJSON(env, "groups", {});
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);
  const targets = resolveTargets(groups, settings);
  const preview =
    `📨 <b>خاموشی جدید دریافت شد</b>\n` +
    `━━━━━━━━━━━━━━\n` +
    `${entry.text}\n` +
    `━━━━━━━━━━━━━━\n\n` +
    `📍 مقصد فعلی: <b>${targets.length}</b> گروه\n` +
    `⏱ ${
      settings.autosend
        ? `اگه ظرف <b>${settings.timeout_minutes || 15}</b> دقیقه پاسخ ندی، خودکار ارسال می‌شه.`
        : "ارسال خودکار خاموشه — منتظر تایید توئه."
    }`;
  const keyboard = {
    inline_keyboard: [[
      { text: "✅ ارسال", callback_data: `send:${pid}` },
      { text: "✏️ ویرایش", callback_data: `edit:${pid}` },
      { text: "❌ لغو", callback_data: `cancel:${pid}` },
    ]],
  };
  await sendToAdmin(env, preview, keyboard);
}

async function handlePendingApi(req, env) {
  if (req.headers.get("X-Reader-Secret") !== env.READER_SECRET) return ERR("unauthorized", 401);
  const body = await req.json();
  if (!body.text) return ERR("missing text");
  const pid = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const entry = { text: body.text, created_at: Date.now(), awaiting_edit: false };
  await setJSON(env, `pending:${pid}`, entry);
  await createPendingPreview(env, pid, entry);
  return OK({ pid });
}

async function handleGroupsApi(req, env) {
  if (req.headers.get("X-Reader-Secret") !== env.READER_SECRET) return ERR("unauthorized", 401);
  const body = await req.json();
  if (!body.chat_id) return ERR("missing chat_id");
  const groups = await getJSON(env, "groups", {});
  groups[body.chat_id] = body.title || body.chat_id;
  await setJSON(env, "groups", groups);
  return OK();
}

async function handleMenuCallback(data, env) {
  const groups = await getJSON(env, "groups", {});
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);

  if (data === "menu:main") return sendToAdmin(env, "🏠 <b>منوی اصلی</b>", mainMenuKeyboard(settings));
  if (data === "menu:groups") return sendToAdmin(env, groupsText(groups), BACK_KEYBOARD);
  if (data === "menu:history") {
    const history = await getJSON(env, "history", []);
    return sendToAdmin(env, historyText(history), BACK_KEYBOARD);
  }
  if (data === "menu:status") {
    const pendingList = await env.BARGH_KV.list({ prefix: "pending:" });
    return sendToAdmin(env, statusText(groups, settings, pendingList.keys.length), BACK_KEYBOARD);
  }
  if (data === "menu:autosend_toggle") {
    settings.autosend = !settings.autosend;
    await setJSON(env, "settings", settings);
    return sendToAdmin(
      env,
      settings.autosend ? "🟢 ارسال خودکار روشن شد." : "🔴 ارسال خودکار خاموش شد.",
      mainMenuKeyboard(settings)
    );
  }
  if (data === "menu:timeout") {
    return sendToAdmin(env, "⏱ چند دقیقه صبر کنه قبل از ارسال خودکار؟", timeoutPickerKeyboard());
  }
  if (data.startsWith("timeout:")) {
    const minutes = parseInt(data.split(":")[1], 10);
    settings.timeout_minutes = minutes;
    await setJSON(env, "settings", settings);
    return sendToAdmin(env, `✅ زمان انتظار روی <b>${minutes}</b> دقیقه تنظیم شد.`, mainMenuKeyboard(settings));
  }
}

async function handleCallbackQuery(cq, env) {
  if (String(cq.from.id) !== String(env.ADMIN_USER_ID)) return;
  const data = cq.data;
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });

  if (data.startsWith("menu:") || data.startsWith("timeout:")) {
    return handleMenuCallback(data, env);
  }

  const [action, pid] = data.split(":");
  const entry = await getJSON(env, `pending:${pid}`, null);
  if (!entry) return sendToAdmin(env, "این پیام قبلاً پردازش شده.");

  const groups = await getJSON(env, "groups", {});
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);

  if (action === "send") {
    const targets = resolveTargets(groups, settings);
    await sendToGroups(env, entry.text, targets);
    await pushHistory(env, { text: entry.text, sent_at: Date.now(), mode: "manual", targets });
    await env.BARGH_KV.delete(`pending:${pid}`);
    await sendToAdmin(env, `✅ به ${targets.length} گروه ارسال شد.`);
  } else if (action === "cancel") {
    await env.BARGH_KV.delete(`pending:${pid}`);
    await sendToAdmin(env, "❌ لغو شد، ارسال نمی‌شود.");
  } else if (action === "edit") {
    entry.awaiting_edit = true;
    await setJSON(env, `pending:${pid}`, entry);
    await sendToAdmin(env, "متن جدید را برایم بفرستید (کل متن جایگزین می‌شود):");
  }
}

async function findAwaitingEdit(env) {
  const list = await env.BARGH_KV.list({ prefix: "pending:" });
  for (const key of list.keys) {
    const entry = await getJSON(env, key.name, null);
    if (entry && entry.awaiting_edit) return { pid: key.name.split(":")[1], key: key.name, entry };
  }
  return null;
}

async function handleTextMessage(text, env) {
  const editing = await findAwaitingEdit(env);
  if (editing) {
    editing.entry.text = text;
    editing.entry.awaiting_edit = false;
    editing.entry.created_at = Date.now();
    await setJSON(env, editing.key, editing.entry);
    const keyboard = {
      inline_keyboard: [[
        { text: "✅ ارسال", callback_data: `send:${editing.pid}` },
        { text: "✏️ ویرایش دوباره", callback_data: `edit:${editing.pid}` },
        { text: "❌ لغو", callback_data: `cancel:${editing.pid}` },
      ]],
    };
    return sendToAdmin(env, `متن ویرایش شد:\n\n${text}`, keyboard);
  }

  const groups = await getJSON(env, "groups", {});
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);

  if (text === "/groups") {
    return sendToAdmin(env, groupsText(groups));
  }
  if (text.startsWith("/setdefault")) {
    const parts = text.split(/\s+/);
    if (parts.length < 2) return sendToAdmin(env, "فرمت درست: /setdefault 1,2  یا  /setdefault all");
    if (parts[1] === "all") {
      settings.default_group_ids = null;
      await setJSON(env, "settings", settings);
      return sendToAdmin(env, "پیش‌فرض پاک شد؛ از این به بعد به همه‌ی گروه‌ها ارسال می‌شود.");
    }
    try {
      const chatIds = Object.keys(groups);
      const selected = parts[1].split(",").map((x) => chatIds[parseInt(x.trim(), 10) - 1]);
      settings.default_group_ids = selected;
      await setJSON(env, "settings", settings);
      return sendToAdmin(env, "از این به بعد فقط به این گروه‌ها ارسال می‌شود:\n" + selected.map((c) => groups[c]).join("\n"));
    } catch (e) {
      return sendToAdmin(env, `خطا در فرمت دستور: ${e}`);
    }
  }
  if (text.startsWith("/setsecret")) {
    const parts = text.split(/\s+/);
    if (parts.length < 3) {
      return sendToAdmin(
        env,
        `فرمت درست: /setsecret NAME value\n\nفقط این سکرت‌ها از تلگرام قابل تغییرن:\n${EDITABLE_SECRETS.join(
          "\n"
        )}`
      );
    }
    const name = parts[1];
    if (!EDITABLE_SECRETS.includes(name)) {
      return sendToAdmin(
        env,
        `❌ سکرت «${name}» از طریق تلگرام قابل تغییر نیست.\n\nفقط این‌ها مجازن:\n${EDITABLE_SECRETS.join("\n")}`
      );
    }
    const value = text.slice(text.indexOf(parts[1]) + parts[1].length + 1);
    try {
      await setGithubSecret(env, name, value);
      return sendToAdmin(env, `✅ سکرت <b>${name}</b> روی گیت‌هاب به‌روزرسانی شد.\n\n⚠️ پیشنهاد می‌کنم همین پیام رو از چت پاک کنی.`);
    } catch (e) {
      return sendToAdmin(env, `❌ خطا در تنظیم سکرت: ${e.message}`);
    }
  }
  if (text.startsWith("/autosend")) {
    const parts = text.split(/\s+/);
    if (parts.length < 2 || !["on", "off"].includes(parts[1])) return sendToAdmin(env, "فرمت درست: /autosend on یا /autosend off");
    settings.autosend = parts[1] === "on";
    await setJSON(env, "settings", settings);
    return sendToAdmin(env, `ارسال خودکار ${settings.autosend ? "روشن" : "خاموش"} شد.`);
  }
  if (text.startsWith("/timeout")) {
    const parts = text.split(/\s+/);
    if (parts.length < 2 || !/^\d+$/.test(parts[1])) return sendToAdmin(env, "فرمت درست: /timeout 15");
    settings.timeout_minutes = parseInt(parts[1], 10);
    await setJSON(env, "settings", settings);
    return sendToAdmin(env, `زمان انتظار روی ${settings.timeout_minutes} دقیقه تنظیم شد.`);
  }
  if (text === "/status") {
    const pendingList = await env.BARGH_KV.list({ prefix: "pending:" });
    return sendToAdmin(env, statusText(groups, settings, pendingList.keys.length));
  }
  if (text === "/history") {
    const history = await getJSON(env, "history", []);
    return sendToAdmin(env, historyText(history));
  }
  if (text === "/help" || text === "/start") {
    return sendToAdmin(
      env,
      "👋 <b>سلام!</b> به ربات مدیریت اطلاع‌رسانی خاموشی برق خوش اومدی.\n\n" +
        "از دکمه‌های زیر استفاده کن، یا این دستورات رو مستقیم بفرست:\n" +
        "<code>/setdefault 1,2</code> - تعیین گروه‌های مقصد پیش‌فرض\n" +
        "<code>/setdefault all</code> - ارسال به همه‌ی گروه‌ها\n" +
        `<code>/setsecret NAME value</code> - تغییر ${EDITABLE_SECRETS.join(" یا ")} روی گیت‌هاب`,
      mainMenuKeyboard(settings)
    );
  }
}

async function handleGroupMessage(msg, env) {
  const text = (msg.text || "").trim();
  const m = text.match(/^\/activate(?:@\w+)?\s+(.+)$/);
  if (!m) return; // فقط به /activate واکنش نشون بده، توی گروه شلوغ نکن
  const code = m[1].trim();
  if (code !== env.ACTIVATION_CODE) return; // کد اشتباهه، بی‌صدا نادیده بگیر

  const chatId = String(msg.chat.id);
  const groups = await getJSON(env, "groups", {});
  if (chatId in groups) {
    return tg(env, "sendMessage", { chat_id: chatId, text: "این گروه از قبل فعال شده." });
  }
  groups[chatId] = msg.chat.title || chatId;
  await setJSON(env, "groups", groups);
  return tg(env, "sendMessage", {
    chat_id: chatId,
    text: "✅ این گروه با موفقیت برای دریافت اطلاع‌رسانی خاموشی برق فعال شد.",
  });
}

async function handleWebhook(req, env) {
  const update = await req.json();
  if (update.callback_query) {
    await handleCallbackQuery(update.callback_query, env);
    return OK();
  }
  const msg = update.message;
  if (!msg) return OK();

  if (msg.chat && (msg.chat.type === "group" || msg.chat.type === "supergroup")) {
    await handleGroupMessage(msg, env);
    return OK();
  }

  if (String(msg.from?.id) !== String(env.ADMIN_USER_ID)) return OK();
  const text = (msg.text || "").trim();
  if (text) await handleTextMessage(text, env);
  return OK();
}

async function checkTimeouts(env) {
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);
  if (!settings.autosend) return;
  const groups = await getJSON(env, "groups", {});
  const timeoutMs = (settings.timeout_minutes || 15) * 60 * 1000;
  const now = Date.now();

  const list = await env.BARGH_KV.list({ prefix: "pending:" });
  for (const key of list.keys) {
    const entry = await getJSON(env, key.name, null);
    if (!entry || entry.awaiting_edit) continue;
    if (now - entry.created_at >= timeoutMs) {
      const targets = resolveTargets(groups, settings);
      await sendToGroups(env, entry.text, targets);
      await pushHistory(env, { text: entry.text, sent_at: now, mode: "auto-timeout", targets });
      await env.BARGH_KV.delete(key.name);
      await sendToAdmin(env, `⏱ زمان تمام شد، پیام خودکار ارسال شد به ${targets.length} گروه.`);
    }
  }
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/api/pending") return handlePendingApi(req, env);
    if (req.method === "POST" && url.pathname === "/api/groups") return handleGroupsApi(req, env);
    if (req.method === "POST" && url.pathname === "/webhook") return handleWebhook(req, env);
    return new Response("bargh-manager worker is running", { status: 200 });
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkTimeouts(env));
  },
};
