/* FCL 前端：原生 JS，不引入构建链。
   三个核心交互：提交批量任务 → 轮询进度 → 渲染卡片并审核。
   面试时可讲：为什么先轮询不用 WebSocket（实现简单、任务短、够用；长任务再换 SSE/WS）。 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  products: [],
  runs: [],
  currentRunId: null,
  pollTimer: null,
  reviewFilter: "all",
  reviewData: [],
};

/* ---------------- 通用 ---------------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text.slice(0, 160)}`);
  }
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2200);
}

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 标签页 ---------------- */
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  const tab = btn.dataset.tab;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab}`));
  if (tab === "dashboard") loadDashboard();
  if (tab === "review") loadReview();
  if (tab === "system") loadSystem();
});

/* ---------------- 商品 ---------------- */
async function loadProducts() {
  state.products = await api("/api/products");
  const sel = $("#product-select");
  sel.innerHTML = state.products
    .map((p) => `<option value="${p.id}">${esc(p.name)} · ${esc(p.category)}</option>`)
    .join("");
  if (!state.products.length) {
    sel.innerHTML = `<option disabled>暂无商品，先新增一个</option>`;
  }
}

$("#add-product").addEventListener("click", async () => {
  const payload = {
    name: $("#p-name").value.trim() || "未命名单品",
    category: $("#p-category").value.trim() || "女装",
    fabric: $("#p-fabric").value.trim(),
    price_band: $("#p-price").value.trim(),
    highlights: $("#p-highlights").value.trim(),
  };
  try {
    await api("/api/products", { method: "POST", body: JSON.stringify(payload) });
    ["#p-name", "#p-category", "#p-fabric", "#p-price", "#p-highlights"].forEach((s) => ($(s).value = ""));
    await loadProducts();
    toast("商品已新增");
  } catch (err) {
    toast("新增失败：" + err.message);
  }
});

/* ---------------- 提交任务 + 轮询 ---------------- */
$("#run-btn").addEventListener("click", async () => {
  const productId = $("#product-select").value;
  if (!productId) return toast("先选一个商品");
  const body = {
    product_id: productId,
    angle: $("#angle").value,
    variants: Number($("#variants").value) || 1,
  };
  const btn = $("#run-btn");
  btn.disabled = true;
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
    state.currentRunId = run.id;
    $("#run-progress").classList.remove("hidden");
    setProgress(0, run.total);
    toast(`任务已入队：${run.total} 条`);
    pollRun(run.id);
  } catch (err) {
    toast("提交失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

function setProgress(done, total) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  $("#progress-bar").style.width = `${pct}%`;
  $("#progress-text").textContent = `已完成 ${done}/${total} · ${pct}%`;
}

function pollRun(runId) {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const detail = await api(`/api/runs/${runId}`);
      setProgress(detail.run.done + detail.run.failed, detail.run.total);
      renderCards($("#cards"), detail.contents, detail.product);
      if (detail.run.finished_at || detail.run.done + detail.run.failed >= detail.run.total) {
        clearInterval(state.pollTimer);
        toast("生成完成");
        loadDashboard();
      }
    } catch (err) {
      clearInterval(state.pollTimer);
      toast("轮询失败：" + err.message);
    }
  }, 900);
}

/* ---------------- 渲染内容卡片 ---------------- */
const STATUS_TEXT = { draft: "待审", rework: "需重写", approved: "已通过", rejected: "已驳回" };

function renderCards(container, contents, product) {
  if (!contents.length) {
    container.innerHTML = `<div class="empty">还没有产出。选个商品，点「开始批量生成」。</div>`;
    return;
  }
  container.innerHTML = contents
    .map((c) => {
      const detail = c.score_detail || {};
      const scores = detail.scores || {};
      const reasons = detail.reasons || {};
      const bad = c.violations || [];
      const badge = c.judge_verdict === "pass" ? "pass" : "rework";
      const dims = Object.entries(scores)
        .map(([k, v]) => `<span title="${esc(reasons[k] || "")}">${DIM_NAME[k] || k} ${Math.round(v)}</span>`)
        .join(" · ");
      return `
      <article class="note" data-id="${c.id}">
        <div class="top">
          <h3 class="title">${esc(c.title) || "（生成中…）"}</h3>
          <div class="score">${c.score_total ? c.score_total.toFixed(0) : "—"}<small>/100</small></div>
        </div>
        <div class="tags">
          <span class="badge ${badge}">${c.judge_verdict === "pass" ? "质检通过" : "建议重写"}</span>
          <span class="badge draft">${STATUS_TEXT[c.status] || c.status}</span>
          ${c.cached ? '<span class="badge cached">缓存命中</span>' : ""}
          ${c.rewrite_rounds ? `<span class="badge rework">重写 ${c.rewrite_rounds} 次</span>` : ""}
        </div>
        <div class="body">${esc(c.body) || "（生成中…）"}</div>
        <div class="tags">${(c.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        ${c.image_prompt ? `<div class="prompt">配图提示词：${esc(c.image_prompt)}</div>` : ""}
        ${bad.length ? `<div class="violations"><b>规则命中：</b>${bad.map((v) => esc(v.word)).join("、")}</div>` : ""}
        <div class="meta">
          <span>${dims}</span>
          <span>tokens <b>${c.tokens_in + c.tokens_out}</b></span>
          <span>耗时 <b>${(c.latency_ms / 1000).toFixed(1)}s</b></span>
          ${c.error ? `<span style="color:var(--red)">失败：${esc(c.error)}</span>` : ""}
        </div>
        <div class="actions">
          <button class="btn small primary" data-act="approve" data-id="${c.id}">通过</button>
          <button class="btn small ghost" data-act="reject" data-id="${c.id}" style="margin-top:0">驳回</button>
          <button class="btn small ghost" data-act="copy" data-id="${c.id}" style="margin-top:0">复制文案</button>
        </div>
      </article>`;
    })
    .join("");
}

const DIM_NAME = { brand_tone: "调性", platform_fit: "平台", info_density: "信息", compliance: "合规" };

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, id } = btn.dataset;
  if (act === "copy") {
    const card = btn.closest(".note");
    const title = card.querySelector(".title").textContent;
    const body = card.querySelector(".body").textContent;
    const tags = [...card.querySelectorAll(".tag")].map((t) => t.textContent).join(" ");
    navigator.clipboard?.writeText(`${title}\n\n${body}\n\n${tags}`);
    toast("已复制，可直接粘到小红书");
    return;
  }
  try {
    await api(`/api/runs/contents/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ action: act }),
    });
    const card = btn.closest(".note");
    card.querySelector(".badge.draft").textContent = act === "approve" ? "已通过" : "已驳回";
    toast(act === "approve" ? "已通过，可复制发布" : "已驳回");
  } catch (err) {
    toast("操作失败：" + err.message);
  }
});

/* ---------------- 看板 ---------------- */
async function loadDashboard() {
  try {
    const d = await api("/api/dashboard");
    const kpis = [
      ["内容总量", d.total_contents, "条", `累计 ${d.runs.total} 个任务`],
      ["一次通过率", (d.first_pass_rate * 100).toFixed(1), "%", "质检达标且无违规"],
      ["缓存命中率", (d.cache_hit_rate * 100).toFixed(1), "%", "省下的重复调用"],
      ["P95 延迟", (d.p95_latency_ms / 1000).toFixed(1), "s", `均值 ${(d.avg_latency_ms / 1000).toFixed(1)}s`],
      ["规则拦截率", (d.rule_block_rate * 100).toFixed(1), "%", "命中违禁词/结构问题"],
      ["平均分", d.avg_score, "分", "四维加权"],
      ["token 消耗", d.total_tokens, "", "输入+输出"],
      ["估算成本", `¥${d.estimated_cost_yuan.toFixed(3)}`, "", `缓存省下 ¥${d.saved_cost_yuan.toFixed(3)}`],
    ];
    $("#kpis").innerHTML = kpis
      .map(
        ([label, value, unit, sub]) => `
      <div class="kpi">
        <div class="label">${label}</div>
        <div class="value">${value}<small>${unit || ""}</small></div>
        <div class="sub">${sub}</div>
      </div>`
      )
      .join("");

    const labels = { brand_tone: "品牌调性", platform_fit: "平台适配", info_density: "信息密度", compliance: "合规安全" };
    $("#dims").innerHTML = Object.entries(d.by_dim)
      .map(
        ([k, v]) => `
      <div class="dim">
        <span>${labels[k] || k}</span>
        <div class="bar"><i style="width:${Math.max(2, v)}%"></i></div>
        <span class="num">${v.toFixed(0)}</span>
      </div>`
      )
      .join("");

    const maxCount = Math.max(1, ...d.trend.map((t) => t.count));
    $("#trend").innerHTML = d.trend.length
      ? d.trend
          .map(
            (t) => `
        <div class="col" title="${t.count} 条 · ¥${t.cost}">
          <i style="height:${Math.max(6, (t.count / maxCount) * 100)}%"></i>
          <span>${t.day}</span>
        </div>`
          )
          .join("")
      : `<div class="empty" style="border:0">还没有数据</div>`;

    $("#events").innerHTML = d.recent_events.length
      ? d.recent_events
          .map(
            (e) => `<li><b>${esc(e.type)}</b><span>${esc(JSON.stringify(e.payload).slice(0, 60))}</span>
            <time>${new Date(e.at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time></li>`
          )
          .join("")
      : `<li class="muted">暂无事件</li>`;

    const q = d.queue || {};
    $("#queue").innerHTML = `
      <span class="k">排队中</span><span class="v">${q.queued ?? 0}</span>
      <span class="k">执行中</span><span class="v">${q.active ?? 0}</span>
      <span class="k">并发上限</span><span class="v">${q.concurrency ?? "-"}</span>
      <span class="k">已处理</span><span class="v">${q.processed ?? 0}</span>
      <span class="k">重试次数</span><span class="v">${q.retried ?? 0}</span>
      <span class="k">限流等待</span><span class="v">${q.rate_limiter?.waited_s ?? 0}s</span>`;
  } catch (err) {
    console.error(err);
  }
}

/* ---------------- 审核队列 ---------------- */
async function loadReview() {
  try {
    const runs = await api("/api/runs?limit=20");
    state.runs = runs;
    const all = [];
    for (const r of runs.slice(0, 6)) {
      const detail = await api(`/api/runs/${r.id}`);
      all.push(...detail.contents);
    }
    state.reviewData = all;
    renderReview();
  } catch (err) {
    toast("加载审核队列失败：" + err.message);
  }
}

function renderReview() {
  const filtered =
    state.reviewFilter === "all" ? state.reviewData : state.reviewData.filter((c) => c.status === state.reviewFilter);
  renderCards($("#review-cards"), filtered);
}

$("#filters").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $$("#filters .chip").forEach((c) => c.classList.toggle("active", c === chip));
  state.reviewFilter = chip.dataset.status;
  renderReview();
});

/* ---------------- 系统状态 ---------------- */
async function loadSystem() {
  try {
    const s = await api("/api/system");
    $("#sys-runtime").innerHTML = `
      <span class="k">模型供应商</span><span class="v">${esc(s.provider)}</span>
      <span class="k">模型</span><span class="v">${esc(s.model)}</span>
      <span class="k">真实计费</span><span class="v">${s.real_llm ? "是" : "否（离线 mock）"}</span>
      <span class="k">提示词版本</span><span class="v">${esc(s.prompt_version)}</span>
      <span class="k">缓存后端</span><span class="v">${esc(s.cache.backend)}</span>
      <span class="k">缓存条目</span><span class="v">${s.cache.items}</span>
      <span class="k">缓存命中率</span><span class="v">${(s.cache.hit_rate * 100).toFixed(1)}%</span>
      <span class="k">通过阈值</span><span class="v">${s.thresholds.pass}</span>`;
    $("#sys-rag").innerHTML = `
      <span class="k">知识库分块</span><span class="v">${s.rag.chunks}</span>
      <span class="k">已向量化</span><span class="v">${s.rag.indexed}</span>
      <span class="k">平均块大小</span><span class="v">${s.rag.avg_tokens} tokens</span>`;
  } catch (err) {
    toast("读取系统状态失败：" + err.message);
  }
}

/* ---------------- 启动 ---------------- */
(async function boot() {
  const conn = $("#conn");
  try {
    await loadProducts();
    await loadDashboard();
    conn.textContent = "服务在线";
    const first = state.products[0];
    if (first) console.log("当前商品：", first.name);
  } catch (err) {
    conn.textContent = "服务异常";
    conn.classList.add("bad");
    console.error(err);
  }
})();
