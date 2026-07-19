// ربات مدیریت اطلاع‌رسانی خاموشی برق — روی Cloudflare Workers
//
// مسیرها:
//   POST /api/pending  <- reader.py (گیت‌هاب) پیام جدید رو اینجا ثبت می‌کنه
//   POST /api/groups   <- reader.py گروه تازه‌فعال‌شده رو اینجا ثبت می‌کنه
//   POST /webhook      <- وب‌هوک تلگرام برای ربات مدیریت (دکمه‌ها و دستورات)
// scheduled()          <- Cron Trigger؛ هر چند دقیقه تایم‌اوت‌ها رو چک می‌کنه

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

function groupListText(groups) {
  const entries = Object.entries(groups);
  if (!entries.length) return "هنوز هیچ گروهی ثبت نشده.";
  return entries.map(([id, title], i) => `${i + 1}. ${title}  (id: ${id})`).join("\n");
}

function resolveTargets(groups, settings) {
  const def = settings.default_group_ids;
  if (def && def.length) return def.filter((id) => id in groups);
  return Object.keys(groups);
}

async function dispatchToGitHub(env, text, targets) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "bargh-manager-worker",
    },
    body: JSON.stringify({
      event_type: env.GITHUB_EVENT_TYPE || "send-outage",
      client_payload: { text, chat_ids: targets },
    }),
  });
  if (!r.ok) throw new Error(`GitHub dispatch failed: ${r.status} ${await r.text()}`);
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
    `📨 پیام جدید خاموشی دریافت شد:\n\n${entry.text}\n\n` +
    `مقصد فعلی: ${targets.length} گروه\n` +
    `⏱ اگه ظرف ${settings.timeout_minutes || 15} دقیقه پاسخ ندید` +
    (settings.autosend ? " و ارسال خودکار روشنه، خودکار ارسال می‌شه." : "، ارسال نمی‌شه (ارسال خودکار خاموشه).");
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

async function handleCallbackQuery(cq, env) {
  if (String(cq.from.id) !== String(env.ADMIN_USER_ID)) return;
  const [action, pid] = cq.data.split(":");
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });

  const entry = await getJSON(env, `pending:${pid}`, null);
  if (!entry) return sendToAdmin(env, "این پیام قبلاً پردازش شده.");

  const groups = await getJSON(env, "groups", {});
  const settings = await getJSON(env, "settings", DEFAULT_SETTINGS);

  if (action === "send") {
    const targets = resolveTargets(groups, settings);
    await dispatchToGitHub(env, entry.text, targets);
    await pushHistory(env, { text: entry.text, sent_at: Date.now(), mode: "manual", targets });
    await env.BARGH_KV.delete(`pending:${pid}`);
    await sendToAdmin(env, `✅ ارسال به ${targets.length} گروه آغاز شد.`);
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
    return sendToAdmin(env, groupListText(groups));
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
    const targets = resolveTargets(groups, settings);
    const pendingList = await env.BARGH_KV.list({ prefix: "pending:" });
    return sendToAdmin(
      env,
      `ارسال خودکار: ${settings.autosend ? "روشن" : "خاموش"}\n` +
        `زمان انتظار: ${settings.timeout_minutes || 15} دقیقه\n` +
        `گروه‌های مقصد فعلی: ${targets.length} از ${Object.keys(groups).length}\n` +
        `پیام‌های در انتظار: ${pendingList.keys.length}`
    );
  }
  if (text === "/history") {
    const history = await getJSON(env, "history", []);
    if (!history.length) return sendToAdmin(env, "هنوز پیامی ارسال نشده.");
    const lines = history.slice(-10).map((h) => {
      const d = new Date(h.sent_at).toISOString().slice(0, 16).replace("T", " ");
      const mode = h.mode === "auto-timeout" ? "خودکار" : "دستی";
      return `[${d}] (${mode}) ${h.text.replace(/<[^>]+>/g, "").slice(0, 50)}...`;
    });
    return sendToAdmin(env, "۱۰ پیام آخر:\n\n" + lines.join("\n\n"));
  }
  if (text === "/help" || text === "/start") {
    return sendToAdmin(
      env,
      "دستورات:\n" +
        "/groups - لیست گروه‌های ثبت‌شده\n" +
        "/setdefault 1,2 - تعیین گروه‌های مقصد پیش‌فرض\n" +
        "/autosend on|off - روشن/خاموش کردن ارسال خودکار\n" +
        "/timeout 15 - تعیین دقیقه‌ی انتظار قبل از ارسال خودکار\n" +
        "/status - وضعیت فعلی\n" +
        "/history - پیام‌های اخیر ارسال‌شده"
    );
  }
}

async function handleWebhook(req, env) {
  const update = await req.json();
  if (update.callback_query) {
    await handleCallbackQuery(update.callback_query, env);
    return OK();
  }
  const msg = update.message;
  if (!msg || String(msg.from?.id) !== String(env.ADMIN_USER_ID)) return OK();
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
      await dispatchToGitHub(env, entry.text, targets);
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
