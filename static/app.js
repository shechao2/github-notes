/* 星驰 4S 店 AI 客服工作台 · 前端逻辑（原生 JS，无构建步骤） */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { sessions: [], session: null, last: null, tickets: [], dash: null, faq: [] };

const DECISION_LABEL = { answered: "直接回答", clarify: "需追问", handoff: "转人工", no_hit: "未命中" };
const STATUS_LABEL = {
  pending: "待审核", accepted: "已采纳", edited: "修改后发送", rejected: "已驳回",
  handed_off: "已转人工", clarify: "追问中", no_hit: "未命中", ok: "用户消息",
};
const PRIORITY_CLASS = { P0: "bad", P1: "warn", P2: "info" };
const TICKET_STATUS_CLASS = { 待处理: "bad", 处理中: "warn", 已解决: "ok", 已关闭: "info" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
    toast(detail, true);
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer = null;
function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 2600);
}

const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const num = (x) => (x == null ? "—" : Number(x).toFixed(1));

/* ---------------- 标签页 ---------------- */
$$(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(name, updateHash = true) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (updateHash && location.hash !== `#${name}`) location.hash = name;
  if (name === "tickets") loadTickets();
  if (name === "dashboard") loadDashboard();
  if (name === "kb") loadFaq();
}

/* ---------------- 会话 ---------------- */
async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.items;
  renderSessions();
}

function renderSessions() {
  const list = $("#sessionList");
  list.innerHTML = "";
  state.sessions.forEach((s) => {
    const li = document.createElement("li");
    li.className = state.session && state.session.id === s.id ? "active" : "";
    li.innerHTML = `<strong>${escapeHtml(s.customer)}</strong>
      <small>${escapeHtml(s.channel)} · ${s.message_count} 条 · ${escapeHtml(s.scene || "未分类")}</small>
      <small>${escapeHtml(s.last_message || "")}</small>`;
    li.addEventListener("click", () => openSession(s.id));
    list.appendChild(li);
  });
  if (!state.sessions.length) {
    list.innerHTML = '<li class="muted">还没有会话，点「重建演示数据」或直接发一个问题。</li>';
  }
}

async function openSession(sessionId) {
  const session = await api(`/api/sessions/${sessionId}`);
  state.session = session;
  renderSessions();
  renderChat();
  const lastAi = [...session.messages].reverse().find((m) => m.role === "ai");
  if (lastAi) {
    state.last = { message: lastAi, decision: lastAi.decision };
    renderDecision();
  }
}

function renderChat() {
  const log = $("#chatLog");
  const session = state.session;
  $("#chatCustomer").textContent = session ? session.customer : "新建会话或选择左侧会话";
  $("#chatMeta").textContent = session ? ` · ${session.channel} · ${session.id}` : "";
  $("#chatScene").textContent = session ? session.scene || "未分类" : "—";
  if (!session) return;
  log.innerHTML = "";
  if (!session.messages.length) {
    log.innerHTML = '<p class="empty">客户还没有发消息，从下方输入。</p>';
    return;
  }
  session.messages.forEach((m) => log.appendChild(bubble(m)));
  log.scrollTop = log.scrollHeight;
}

function bubble(m) {
  const div = document.createElement("div");
  div.className = `bubble ${m.role}`;
  const text = document.createElement("span");
  text.textContent = m.text;
  div.appendChild(text);

  if (m.role === "ai") {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${STATUS_LABEL[m.status] || m.status} · 置信度 ${(m.confidence * 100).toFixed(0)}% · ${m.intent || "未识别"}`;
    div.appendChild(meta);
    if (m.faq_ids && m.faq_ids.length) {
      const cite = document.createElement("span");
      cite.className = "cite";
      cite.textContent = "依据：" + m.faq_ids.join("、");
      div.appendChild(cite);
    }
    div.style.cursor = "pointer";
    div.addEventListener("click", () => {
      state.last = { message: m, decision: m.decision };
      renderDecision();
    });
  }
  return div;
}

/* ---------------- 聊天 ---------------- */
$("#chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try {
    const out = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ text, session_id: state.session ? state.session.id : null }),
    });
    state.last = { message: out.ai_message, decision: out.decision, ticket: out.ticket };
    await openSession(out.session_id);
    renderDecision();
    loadSessions();
  } catch (_) { /* toast 已提示 */ }
});

$("#newSession").addEventListener("click", async () => {
  const customer = prompt("客户称呼（可留空）", "");
  const out = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({ text: "你好", session_id: null, customer: customer || null }),
  });
  state.last = { message: out.ai_message, decision: out.decision, ticket: out.ticket };
  await openSession(out.session_id);
  renderDecision();
  loadSessions();
});

/* ---------------- 决策面板 ---------------- */
function renderDecision() {
  const panel = $("#decisionPanel");
  const tag = $("#decisionTag");
  const last = state.last;
  if (!last) {
    panel.className = "decision-empty";
    panel.textContent = "发送问题后，这里会显示意图、置信度、命中的 FAQ 证据和审核动作。";
    tag.textContent = "";
    return;
  }
  const d = last.decision || {};
  const m = last.message || {};
  const status = d.status || "pending";
  panel.className = "decision";
  tag.textContent = d.priority || "";

  const badgeClass = status === "answered" ? "ok" : status === "clarify" ? "warn" : status === "no_hit" ? "bad" : "info";
  const riskText = (d.risk && d.risk.reasons && d.risk.reasons.length) ? d.risk.reasons.join("；") : "无";
  const evidence = (d.evidence || []).map((e) => `
      <div class="evidence">
        <b>${escapeHtml(e.faq_id)}</b> · ${escapeHtml(e.question)}
        <p>${escapeHtml(e.answer)}</p>
        <small>来源 ${escapeHtml(e.source)} · 负责人 ${escapeHtml(e.owner)} · 更新 ${escapeHtml(e.updated_at)} · 置信度 ${(e.confidence * 100).toFixed(0)}%</small>
      </div>`).join("") || '<p class="muted small">没有命中的 FAQ 条目。</p>';

  const reviewButtons = m.status === "pending" ? `
      <div class="actions">
        <button data-act="accepted">采纳并发送</button>
        <button data-act="edited" class="neutral">修改后发送</button>
        <button data-act="rejected" class="danger">驳回</button>
        <button data-act="handed_off" class="neutral">转人工</button>
      </div>
      <textarea class="reply-edit hidden" id="editBox" placeholder="修改后的回复"></textarea>`
    : `<p class="muted small">该条已处理：${escapeHtml(STATUS_LABEL[m.status] || m.status)}</p>`;

  panel.innerHTML = `
    <div class="row"><span class="badge ${badgeClass}">${DECISION_LABEL[status] || status}</span>
      <strong>${(d.confidence * 100).toFixed(0)}%</strong></div>
    <div class="conf"><i style="width:${Math.min(100, (d.confidence || 0) * 100)}%"></i></div>
    <dl class="kv">
      <dt>意图</dt><dd>${escapeHtml(d.intent || "—")}</dd>
      <dt>场景</dt><dd>${escapeHtml(d.scene || "—")}</dd>
      <dt>车型</dt><dd>${escapeHtml((d.models || []).join("、") || "未提及")}</dd>
      <dt>情绪</dt><dd>${escapeHtml(d.emotion || "—")}</dd>
      <dt>风险</dt><dd>${escapeHtml(riskText)}</dd>
      <dt>优先级</dt><dd>${escapeHtml(d.priority || "—")}</dd>
      <dt>应答方式</dt><dd>${d.llm && d.llm.used ? "大模型改写（" + escapeHtml(d.llm.model || "deepseek") + "）" : "规则拼接"}</dd>
    </dl>
    ${d.conflict ? '<p class="badge bad">知识冲突：前两条答案不一致</p>' : ""}
    ${d.handoff_reason ? `<p class="muted small">转人工原因：${escapeHtml(d.handoff_reason)}</p>` : ""}
    ${(d.clarify || []).length ? `<p class="muted small">追问：${escapeHtml(d.clarify.join(" / "))}</p>` : ""}
    <div class="panel-head" style="padding:0;border:0;margin-top:4px"><span>命中证据</span></div>
    ${evidence}
    <div class="panel-head" style="padding:0;border:0;margin-top:4px"><span>客服审核</span></div>
    ${reviewButtons}
    <div class="panel-head" style="padding:0;border:0;margin-top:4px"><span>客户反馈</span></div>
    <div class="actions">
      <button data-fb="solved" class="neutral">已解决</button>
      <button data-fb="unsolved" class="neutral">未解决</button>
      <button data-fb="wrong" class="danger">答案错误</button>
    </div>
    ${last.ticket ? `<p class="muted small">已建工单 ${escapeHtml(last.ticket.no)}（${escapeHtml(last.ticket.priority)}）</p>` : ""}
  `;

  $$("#decisionPanel [data-act]").forEach((btn) => {
    btn.addEventListener("click", () => review(btn.dataset.act));
  });
  $$("#decisionPanel [data-fb]").forEach((btn) => {
    btn.addEventListener("click", () => feedback(btn.dataset.fb));
  });
}

async function review(action) {
  const m = state.last && state.last.message;
  if (!m) return;
  let text = null;
  if (action === "edited") {
    const box = $("#editBox");
    box.classList.remove("hidden");
    if (!box.value.trim()) {
      box.value = m.text;
      toast("已把 AI 原文放进编辑框，改完再点一次「修改后发送」");
      return;
    }
    text = box.value.trim();
  }
  if ((action === "rejected" || action === "handed_off") && !confirm("确认提交？会生成人工工单。")) return;
  const out = await api(`/api/messages/${m.id}/review`, {
    method: "POST",
    body: JSON.stringify({ action, text }),
  });
  state.last = { message: out.message, decision: out.message.decision, ticket: out.ticket || state.last.ticket };
  toast("审核已记录：" + (STATUS_LABEL[out.message.status] || out.message.status));
  if (state.session) await openSession(state.session.id);
  renderDecision();
  loadTickets();
  loadDashboard();
}

async function feedback(result) {
  const m = state.last && state.last.message;
  if (!m) return;
  const out = await api(`/api/messages/${m.id}/feedback`, {
    method: "POST",
    body: JSON.stringify({ result }),
  });
  toast(result === "solved" ? "已记录：客户认为已解决" : "已记录反馈，必要时已建工单");
  if (out.ticket) state.last.ticket = out.ticket;
  if (state.session) await openSession(state.session.id);
  renderDecision();
  loadTickets();
  loadDashboard();
}

/* ---------------- 工单 ---------------- */
async function loadTickets() {
  const data = await api("/api/tickets");
  state.tickets = data.items;
  renderTickets();
}

function slaText(t) {
  const first = t.sla && t.sla.first_response;
  if (!first) return "—";
  if (t.status === "已解决" || t.status === "已关闭") return "已关闭";
  if (first.overdue) return `超时 ${Math.abs(first.remaining_minutes)} 分钟`;
  return `剩 ${first.remaining_minutes} 分钟`;
}

function renderTickets() {
  const table = $("#ticketTable");
  $("#ticketCount").textContent = `${state.tickets.length} 条`;
  if (!state.tickets.length) {
    table.innerHTML = '<tbody><tr><td class="muted">暂无工单</td></tr></tbody>';
    return;
  }
  const head = "<thead><tr><th>编号</th><th>客户问题</th><th>意图</th><th>优先级</th><th>状态</th><th>负责人</th><th>首响 SLA</th></tr></thead>";
  const rows = state.tickets.map((t) => `
    <tr data-id="${t.id}">
      <td>${escapeHtml(t.no)}</td>
      <td>${escapeHtml(t.question).slice(0, 40)}</td>
      <td>${escapeHtml(t.intent)}</td>
      <td><span class="badge ${PRIORITY_CLASS[t.priority] || "info"}">${t.priority}</span></td>
      <td><span class="badge ${TICKET_STATUS_CLASS[t.status] || "info"}">${t.status}</span></td>
      <td>${escapeHtml(t.owner)}</td>
      <td>${slaText(t)}</td>
    </tr>`).join("");
  table.innerHTML = head + `<tbody>${rows}</tbody>`;
  const first = state.tickets[0];
  if (first && !$("#ticketDetail").querySelector("h3")) renderTicketDetail(first.id);
  $$("#ticketTable tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      $$("#ticketTable tbody tr").forEach((x) => x.classList.remove("selected"));
      tr.classList.add("selected");
      renderTicketDetail(tr.dataset.id);
    });
  });
}

async function renderTicketDetail(ticketId) {
  const t = await api(`/api/tickets/${ticketId}`);
  const box = $("#ticketDetail");
  box.className = "";
  box.innerHTML = `
    <h3 style="margin:0 0 6px">${escapeHtml(t.no)} <span class="badge ${PRIORITY_CLASS[t.priority] || "info"}">${t.priority}</span></h3>
    <p class="muted small">${escapeHtml(t.intent)} · ${escapeHtml(t.scene || "未分类")} · ${escapeHtml(t.model || "未提及车型")}</p>
    <dl class="kv" style="margin:10px 0">
      <dt>客户问题</dt><dd>${escapeHtml(t.question)}</dd>
      <dt>转人工原因</dt><dd>${escapeHtml(t.handoff_reason || "—")}</dd>
      <dt>AI 回答</dt><dd>${escapeHtml(t.ai_answer || "—")}</dd>
      <dt>首响时限</dt><dd>${escapeHtml((t.sla.first_response.due || "—"))}（${escapeHtml(slaText(t))}）</dd>
      <dt>解决时限</dt><dd>${escapeHtml(t.sla.resolve.due || "—")}</dd>
    </dl>
    <div class="kb-form" style="background:transparent;padding:0;border:0">
      <label class="muted small">状态
        <select id="tkStatus">${["待处理", "处理中", "已解决", "已关闭"].map((s) => `<option ${s === t.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      </label>
      <label class="muted small">负责人 <input id="tkOwner" value="${escapeHtml(t.owner)}" /></label>
      <label class="muted small">根因 <input id="tkRoot" value="${escapeHtml(t.root_cause)}" placeholder="根因分类" /></label>
      <label class="muted small">最终解决方案 <textarea id="tkSolution" placeholder="填写后可一键回写 FAQ 草稿">${escapeHtml(t.solution)}</textarea></label>
      <label class="muted small">满意度
        <select id="tkSat">
          <option value="">未回访</option>
          ${[5, 4, 3, 2, 1].map((n) => `<option ${t.satisfaction === n ? "selected" : ""} value="${n}">${n} 分</option>`).join("")}
        </select>
      </label>
    </div>
    <div class="actions" style="margin-top:10px">
      <button id="tkSave">保存</button>
      <button id="tkDraft" class="neutral">生成 FAQ 草稿</button>
    </div>
    ${t.suggest_faq ? '<p class="muted small">该工单已回写 FAQ 草稿，去「知识库」审核发布。</p>' : ""}
  `;
  $("#tkSave").addEventListener("click", async () => {
    const patch = {
      status: $("#tkStatus").value,
      owner: $("#tkOwner").value,
      root_cause: $("#tkRoot").value,
      solution: $("#tkSolution").value,
    };
    const sat = $("#tkSat").value;
    if (sat) patch.satisfaction = Number(sat);
    await api(`/api/tickets/${ticketId}`, { method: "PATCH", body: JSON.stringify(patch) });
    toast("工单已更新");
    await loadTickets();
    await renderTicketDetail(ticketId);
    loadDashboard();
  });
  $("#tkDraft").addEventListener("click", async () => {
    try {
      const draft = await api(`/api/tickets/${ticketId}/faq-draft`, { method: "POST" });
      toast(`已生成 FAQ 草稿 ${draft.id}，去知识库审核发布`);
      await renderTicketDetail(ticketId);
    } catch (_) { /* toast 已提示 */ }
  });
}

/* ---------------- 看板 ---------------- */
async function loadDashboard() {
  try {
    const data = await api("/api/dashboard?days=30");
    state.dash = data;
    renderDashboard();
  } catch (_) { /* ignore */ }
}

function renderDashboard() {
  const d = state.dash;
  if (!d) return;
  const kpis = [
    ["会话量", d.sessions, "近 30 天"],
    ["自动解决率", pct(d.auto_resolve_rate), "采纳+修改 / 已审核"],
    ["转人工率", pct(d.handoff_rate), "按 AI 建议数计"],
    ["FAQ 命中率", pct(d.faq_hit_rate), "置信度 ≥ 阈值"],
    ["平均满意度", d.avg_satisfaction == null ? "—" : d.avg_satisfaction + " / 5", "回访评分"],
    ["平均首响", d.avg_first_response_minutes == null ? "—" : num(d.avg_first_response_minutes) + " 分钟", "工单首次响应"],
    ["工单", `${d.tickets.open} / ${d.tickets.total}`, "未结 / 总数"],
    ["SLA 超时", d.tickets.overdue_first_response + d.tickets.overdue_resolve, "首响 + 解决"],
  ];
  $("#kpiGrid").innerHTML = kpis.map(([label, value, hint]) => `
    <div class="kpi"><span>${label}</span><strong>${value}</strong><small>${hint}</small></div>`).join("");

  const maxIntent = Math.max(1, ...d.top_intents.map((x) => x.count));
  $("#topIntents").innerHTML = d.top_intents.length
    ? d.top_intents.map((x) => `
      <div class="bar-row"><span>${escapeHtml(x.intent)}</span>
        <div class="bar"><i style="width:${(x.count / maxIntent) * 100}%"></i></div>
        <span>${x.count}</span></div>`).join("")
    : '<p class="muted small">暂无数据</p>';

  $("#unmatched").innerHTML = d.unmatched_questions.length
    ? d.unmatched_questions.map((x) => `<div><span>${escapeHtml(x.question)}</span><b>${x.count}</b></div>`).join("")
    : '<p class="muted small">暂无未命中问题</p>';

  $("#repeatHandoff").innerHTML = d.repeat_handoff.length
    ? d.repeat_handoff.map((x) => `<div><span>${escapeHtml(x.intent)}</span><b>${x.count} 单</b></div>`).join("")
    : '<p class="muted small">暂无重复转人工</p>';

  $("#faqStats").innerHTML = `
    <div><span>FAQ 总数（生效）</span><b>${d.faq.total}</b></div>
    <div><span>新增 FAQ</span><b>${d.faq.new}</b></div>
    <div><span>更新 FAQ</span><b>${d.faq.updated}</b></div>
    <div><span>待审核草稿</span><b>${d.faq.pending_review}</b></div>
    <div><span>工单总量</span><b>${d.tickets.total}</b></div>`;
  $("#dashNote").textContent = `统计区间：近 ${d.range_days} 天 · 置信度阈值 ${(d.thresholds.confidence * 100).toFixed(0)}% · 生成于 ${d.generated_at}`;
}

/* ---------------- 知识库 ---------------- */
async function loadFaq() {
  const q = $("#kbSearch").value.trim();
  const status = $("#kbStatus").value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  const data = await api(`/api/faq?${params.toString()}`);
  state.faq = data.items;
  state.faqCount = data.items.length;
  renderFaq();
  if (state.dash) loadDashboard();
}

function renderFaq() {
  const table = $("#kbTable");
  $("#kbCount").textContent = `${state.faq.length} 条`;
  if (!state.faq.length) {
    table.innerHTML = '<tbody><tr><td class="muted">没有匹配的 FAQ</td></tr></tbody>';
    return;
  }
  const head = "<thead><tr><th>ID</th><th>分类</th><th>标准问题</th><th>负责人</th><th>更新</th><th>命中</th><th>状态</th><th>操作</th></tr></thead>";
  const rows = state.faq.map((e) => `
    <tr>
      <td>${escapeHtml(e.id)}</td>
      <td>${escapeHtml(e.category)}</td>
      <td><b>${escapeHtml(e.question)}</b><br><small class="muted">${escapeHtml((e.variants || []).slice(0, 3).join(" / "))}</small></td>
      <td>${escapeHtml(e.owner)}</td>
      <td>${escapeHtml(e.updated_at)}</td>
      <td>${e.hits || 0}</td>
      <td><span class="badge ${e.status === "生效" ? "ok" : e.status === "待审核" ? "warn" : "info"}">${escapeHtml(e.status)}</span></td>
      <td>${e.status === "待审核" ? `<button class="small" data-publish="${e.id}">发布</button>` : ""}</td>
    </tr>`).join("");
  table.innerHTML = head + `<tbody>${rows}</tbody>`;
  $$("#kbTable [data-publish]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/faq/${btn.dataset.publish}/publish`, { method: "POST" });
      toast("FAQ 已发布生效");
      loadFaq();
    });
  });
}

$("#kbSearch").addEventListener("input", debounce(loadFaq, 250));
$("#kbStatus").addEventListener("change", loadFaq);
$("#kbNewBtn").addEventListener("click", () => $("#kbForm").classList.toggle("hidden"));
$("#kbForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    question: $("#kbQuestion").value.trim(),
    answer: $("#kbAnswer").value.trim(),
    category: $("#kbCategory").value.trim() || "其他",
    owner: $("#kbOwner").value.trim() || "待指派",
    tags: $("#kbTags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    source: $("#kbSource").value.trim() || "人工录入",
    status: $("#kbNewStatus").value,
  };
  if (!payload.question || !payload.answer) return toast("问题和答案都要填", true);
  await api("/api/faq", { method: "POST", body: JSON.stringify(payload) });
  toast("FAQ 已保存");
  $("#kbForm").reset();
  $("#kbForm").classList.add("hidden");
  loadFaq();
});

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/* ---------------- 初始化 ---------------- */
$("#seedBtn").addEventListener("click", async () => {
  if (!confirm("会用演示数据覆盖当前会话、工单和 FAQ，确定吗？")) return;
  const out = await api("/api/demo/seed?reset=true", { method: "POST" });
  toast(`演示数据已重建：${out.sessions} 个会话 / ${out.tickets} 个工单`);
  state.session = null;
  state.last = null;
  await loadSessions();
  await loadFaq();
  await loadTickets();
  await loadDashboard();
  renderChat();
  renderDecision();
});

async function loadFeishuStatus() {
  try {
    const status = await api("/api/feishu/status");
    state.feishu = status;
    const btn = $("#syncFeishu");
    if (status.configured && status.base_ready) {
      btn.style.display = "";
      btn.title = "写入多维表格：" + (status.base_url || "");
    }
  } catch (_) { /* 未配置飞书时静默 */ }
}

$("#syncFeishu").addEventListener("click", async () => {
  if (!state.session) return toast("先选一个会话", true);
  const btn = $("#syncFeishu");
  btn.disabled = true;
  try {
    const out = await api(`/api/feishu/sync?session_id=${state.session.id}`, { method: "POST" });
    toast(`已同步到多维表格（record ${out.record_id}）`);
  } catch (_) { /* toast 已提示 */ }
  btn.disabled = false;
});

async function init() {
  await loadFeishuStatus();
  try {
    const conf = await api("/api/config");
    $("#thresholdChip").textContent = `置信度阈值 ${(conf.confidence_threshold * 100).toFixed(0)}% · 追问线 ${(conf.clarify_threshold * 100).toFixed(0)}%`;
  } catch (_) { /* ignore */ }
  await loadFaq();
  const health = await api("/api/health");
  if (!health.sessions) {
    const out = await api("/api/demo/seed?reset=true", { method: "POST" });
    toast(`首次启动，已载入演示数据（${out.sessions} 个会话）`);
  }
  await loadSessions();
  await loadTickets();
  await loadDashboard();

  const hash = location.hash.replace("#", "");
  if (["workbench", "tickets", "dashboard", "kb"].includes(hash)) switchTab(hash, false);
  if (state.sessions.length) await openSession(state.sessions[0].id);
}

window.addEventListener("hashchange", () => {
  const hash = location.hash.replace("#", "");
  if (["workbench", "tickets", "dashboard", "kb"].includes(hash)) switchTab(hash, false);
});

init();
