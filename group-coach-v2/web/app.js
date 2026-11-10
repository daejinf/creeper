let busyAction = null;
const $ = (id) => document.getElementById(id),
  uuid = () => crypto.randomUUID(),
  KEY = "wemeet-coach-v2";
let group = null,
  owner = null,
  personal = null,
  busy = false,
  privateEpoch = 0,
  scenarios = [],
  selectedScenario = null,
  editing = null;
function node(tag, text) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
}
function button(text, fn, cls = "") {
  const b = node("button", text);
  b.type = "button";
  b.className = cls;
  b.onclick = fn;
  return b;
}
function inlineText(parent, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*)/g)) {
    if (part.startsWith("**") && part.endsWith("**"))
      parent.append(node("strong", part.slice(2, -2)));
    else parent.append(document.createTextNode(part));
  }
}
function coachText(text, fold = true) {
  const box = node("div");
  box.className = "coachText";
  if (fold && text.length > 1400) {
    const cut = text.indexOf("\n\n", Math.min(500, text.length));
    const end = cut < 0 ? 650 : Math.min(cut, 850);
    box.append(coachText(text.slice(0, end), false));
    const more = node("details");
    more.className = "coachMore";
    more.append(node("summary", "전체 코칭 펼쳐보기"), coachText(text, false));
    box.append(more);
    return box;
  }
  let list = null;
  for (const line of text.split("\n")) {
    const value = line.trim();
    if (!value) {
      list = null;
      continue;
    }
    const heading = /^#{1,4}\s+(.+)$/.exec(value);
    if (heading) {
      list = null;
      const h = node("h3");
      inlineText(h, heading[1]);
      box.append(h);
      continue;
    }
    const bullet = /^(?:[-*•]|\d+[.)])\s+(.+)$/.exec(value);
    if (bullet) {
      if (!list) {
        list = node("ul");
        box.append(list);
      }
      const item = node("li");
      inlineText(item, bullet[1]);
      list.append(item);
      continue;
    }
    list = null;
    const p = node("p");
    inlineText(p, value.replace(/^>\s*/, ""));
    box.append(p);
  }
  return box;
}
function notice(text, error = false) {
  $("notice").hidden = false;
  $("notice").textContent = text;
  $("notice").style.background = error ? "#ffeded" : "#eff4ff";
}
async function api(path, body) {
  const r = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const v = await r.json();
  if (!r.ok) throw Error(v.error?.message || "요청을 완료하지 못했습니다.");
  return v;
}
function endpoint(s = "") {
  return "/api/groups/" + group.group_id + s;
}
function actor() {
  return $("actor").value;
}
function used(result, target = "usage") {
  const m = result.meta;
  const p = result.persistence;
  const text =
    result.reused || result.new_http === 0
      ? "같은 동작의 저장 결과 복구 · 새 HTTP 0회"
      : m
        ? `${m.execution_source} · ${m.model || "코드 처리"} · ${m.usage?.total_tokens || 0} tokens · HTTP ${m.transport_attempts || 0}회 · ${m.latency_ms}ms`
        : "실행 상태 " + (p?.state || "확인 필요");
  const saved = p
    ? p.state === "COMPLETE"
      ? result.status === "FAILED"
        ? " · 실패 기록 저장됨 · 새 결과 없음"
        : " · 저장 완료"
      : " · 저장 " + p.state
    : "";
  $(target).textContent = text + saved;
  $(target === "privateStatus" ? "privateResponse" : "response").textContent =
    JSON.stringify(result, null, 2);
  if (target === "privateStatus") {
    if (result.error) $(target).textContent += " · " + result.error.message;
    if (result.status === "NEEDS_INPUT")
      $(target).textContent += " · " + (result.needed_inputs || []).join(" · ");
    return;
  }
  if (result.error) notice(result.error.message, true);
  else if (p?.state === "STALE")
    notice(
      "응답을 보관했지만 실행 중 입력이 바뀌었습니다. 최신 기록으로 다시 검토해주세요.",
      true,
    );
  else if (result.status === "NEEDS_INPUT")
    notice(
      result.needed_inputs?.join(" · ") ||
        "같은 동작의 진행 상태를 확인해주세요.",
    );
}
async function runAI(module, params = {}, privateOwner = null) {
  if (busy) return;
  busy = true;
  busyAction = module;
  if (privateOwner) renderQuestion();
  $("progress").textContent = "실제 AI가 대화를 읽고 있어요…";
  document
    .querySelectorAll(".aiActions button")
    .forEach((b) => (b.disabled = true));
  const started = Date.now();
  const waiting = privateOwner
    ? setInterval(() => {
        if (owner === privateOwner)
          $("privateStatus").textContent =
            (module === "PLAN"
              ? "AI가 담당 범위와 시간 예산에 맞춰 개인 계획을 작성하고 있어요 · "
              : "AI가 답변을 반영하고 다음 질문을 준비하고 있어요 · ") +
            Math.floor((Date.now() - started) / 1000) +
            "초";
      }, 1000)
    : null;
  if (privateOwner) {
    $("privateStatus").textContent =
      module === "PLAN"
        ? "개인 계획을 작성하고 있어요…"
        : "답변을 반영하고 있어요…";
    $("privateForm").setAttribute("aria-busy", "true");
    $("privateForm").querySelector("button").disabled = true;
    $("beginPrivate").disabled = true;
    $("makePlan").disabled = true;
  }
  const gid = group.group_id,
    key =
      KEY + ":pending:" + gid + ":" + (privateOwner || "group") + ":" + module,
    signature = JSON.stringify({ ...params, message_id: undefined });
  let pending = JSON.parse(localStorage.getItem(key) || "null");
  if (!pending || pending.signature !== signature) {
    pending = { signature, action_id: uuid(), params };
    localStorage.setItem(key, JSON.stringify(pending));
  }
  params = pending.params;
  try {
    const latest = await api(endpoint());
    group = latest;
    const url = privateOwner
      ? endpoint("/personal/" + encodeURIComponent(privateOwner) + "/ai")
      : endpoint("/ai");
    const value = await api(url, {
      action: module,
      action_id: pending.action_id,
      expected_revision: group.revision,
      ...params,
    });
    if (
      !["RUNNING", "UNKNOWN", "SAVE_PENDING"].includes(
        value.result.persistence?.state,
      )
    )
      localStorage.removeItem(key);
    if (group.group_id !== gid) return;
    render(value.group);
    if (!privateOwner) used(value.result);
    if (privateOwner && owner === privateOwner) {
      personal = value.personal;
      renderPersonal();
      used(value.result, "privateStatus");
      if (module === "PLAN" && value.result.data?.plan)
        $("personalPlans").scrollIntoView({
          block: "start",
          behavior: "smooth",
        });
    }
    return value;
  } catch (e) {
    if (!privateOwner)
      notice(
        e.message + " 같은 요청으로 다시 누르면 저장 결과부터 확인합니다.",
        true,
      );
    else $("privateStatus").textContent = e.message;
  } finally {
    if (waiting) clearInterval(waiting);
    $("privateForm").removeAttribute("aria-busy");
    $("privateForm").querySelector("button").disabled = false;
    $("beginPrivate").disabled = false;
    $("makePlan").disabled = false;
    busy = false;
    $("progress").textContent = "";
    document
      .querySelectorAll(".aiActions button")
      .forEach((b) => (b.disabled = false));
    if (personal) renderQuestion();
  }
}
function summaryText(summary, fallback = "") {
  if (!summary) return fallback;
  const labels = {
    established: "정해진 내용",
    decisions_needed: "결정할 내용",
    refinements: "더 구체화할 내용",
    next_actions: "다음 행동",
  };
  const sections = Object.entries(labels)
    .filter(([key]) => summary[key]?.length)
    .map(
      ([key, label]) =>
        label + "\n" + summary[key].map((v) => "• " + v).join("\n"),
    );
  return sections.length ? sections.join("\n\n") : fallback;
}
function bubble(event, container, privateMode = false) {
  if (event.kind === "MESSAGE" || event.kind === "AI_REPLY") {
    const wrap = node("article");
    wrap.className = "message" + (event.kind === "AI_REPLY" ? " ai" : "");
    const m = group.members.find((m) => m.member_id === event.author);
    wrap.append(
      node(
        "small",
        event.kind === "AI_REPLY"
          ? "WE·MEET AI" + (event.body.stale ? " · 이전 입력 기준" : "")
          : m?.nickname || "나",
      ),
    );
    const text = node("div");
    text.className = "bubble";
    if (event.kind === "AI_REPLY") {
      text.append(coachText(summaryText(event.body.summary, event.body.text)));
      if (event.body.details) {
        const details = node("details");
        details.append(
          node("summary", "판단 근거·대안 자세히 보기"),
          coachText(event.body.details, false),
        );
        text.append(details);
      }
    } else text.textContent = event.body.text;
    wrap.append(text);
    container.append(wrap);
  } else if (!privateMode && event.kind === "GROUP_PROPOSAL") {
    const current = group.proposal?.id === event.body.proposal_id;
    container.append(
      proposalCard(
        event.body.proposal,
        current ? group.proposal : null,
        event.body.version,
      ),
    );
  } else if (!privateMode && event.kind === "PERSONAL_PLAN_SHARED") {
    const s = event.body.summary,
      c = node("article");
    c.className = "proposal";
    c.append(
      node("span", "개인 계획 공유 · 본인 확인 완료"),
      node("h2", s.nickname + " · " + s.contribution),
      node("p", s.deliverable),
    );
    for (const t of s.tasks) c.append(node("p", `${t.due_date} · ${t.title}`));
    container.append(c);
  } else if (!privateMode && event.kind === "VOTE_RESULT") {
    const c = node("article");
    c.className = "proposal";
    c.append(node("h3", event.body.text));
    for (const v of event.body.proposal.votes || [])
      c.append(
        node(
          "p",
          v.nickname +
            " · " +
            (v.agree ? "찬성" : "반대") +
            (v.reason ? " · " + v.reason : ""),
        ),
      );
    container.append(c);
  } else {
    const p = node(
      "p",
      (event.kind === "AI_ERROR" ? "실행 오류 기록 · " : "") +
        (event.body.text || ""),
    );
    p.className = "system";
    container.append(p);
  }
}
function proposalCard(p, current, version) {
  const c = node("article");
  c.className = "proposal";
  const tag = node(
    "span",
    `공동 계획 v${version} · ${current ? { VOTING: "전원 동의 대기", APPROVED: "전원 찬성 완료", REJECTED: "수정 필요", SUPERSEDED: "이전 제안" }[current.state] : "보관된 제안"}`,
  );
  tag.className = "tag";
  c.append(
    tag,
    node("h2", p.goal.title),
    node("p", `${p.goal.start_date} — ${p.goal.end_date}`),
  );
  const ul = node("ul");
  for (const text of p.goal.completion_criteria) ul.append(node("li", text));
  c.append(ul);
  const names = Object.fromEntries(
    group.members.map((m) => [m.member_id, m.nickname]),
  );
  c.append(
    node(
      "p",
      "담당: " +
        Object.entries(p.goal.roles)
          .map(([id, r]) => (names[id] || "구성원") + " · " + r)
          .join(" / "),
    ),
  );
  const details = node("details");
  details.append(node("summary", "일정·작업·인증 확인"));
  for (const t of p.tasks) {
    const d = node("div");
    d.className = "task";
    d.append(
      node("strong", t.title),
      node("p", t.due_date + " · " + (names[t.owner_member_id] || "함께")),
      node("p", t.deliverable),
      node("p", t.completion_criteria.join(" · ")),
    );
    details.append(d);
  }
  details.append(
    node("p", "수행: " + p.goal.execution_method),
    node("p", "인증: " + p.goal.verification_method),
  );
  c.append(details);
  if (p.recommendations.length) {
    const d = node("details");
    d.append(node("summary", "AI 추천 · 동의 전"));
    for (const x of p.recommendations) d.append(node("p", x));
    c.append(d);
  }
  if (p.change_summary.length)
    c.append(node("p", "변경 사항: " + p.change_summary.join(" / ")));
  if (current) {
    const count = node(
      "div",
      `${current.participation}/${current.total}명 응답 · ${current.state === "VOTING" ? "종료 후 이름별 선택 공개" : "투표 종료"}`,
    );
    count.className = "voteCount";
    c.append(count);
    for (const x of current.issues || [])
      c.append(node("p", "익명 수정 의견 · " + x.issue + ": " + x.reason));
    const row = node("div");
    row.className = "toolbar";
    if (current.state === "VOTING")
      row.append(
        button("찬성", () => castVote(true)),
        button("반대 · 이유 쓰기", () => $("voteDialog").showModal()),
      );
    if (current.state === "REJECTED" || current.issues?.length)
      row.append(
        button(
          "반대 의견 반영해 수정안 요청",
          () => runAI("REVISE"),
          "primary",
        ),
      );
    row.append(button("직접 수정해 새 버전 게시", () => editProposal(p)));
    c.append(row);
  }
  return c;
}
function render(view) {
  const changed = group?.group_id !== view.group_id;
  group = view;
  localStorage.setItem(KEY + ":group", view.group_id);
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  $("roomTitle").textContent = view.title;
  $("roomMembers").textContent =
    view.members.map((m) => m.nickname).join(" · ") +
    " · " +
    view.members.length +
    "명";
  const old = changed
    ? localStorage.getItem(KEY + ":actor:" + view.group_id) || ""
    : $("actor").value;
  $("actor").replaceChildren(
    ...view.members.map((m) => {
      const o = node("option", m.nickname);
      o.value = m.member_id;
      return o;
    }),
  );
  $("actor").value = view.members.some((m) => m.member_id === old)
    ? old
    : view.members[0].member_id;
  $("saved").textContent = "PostgreSQL 저장됨 · v" + view.revision;
  $("personal").disabled = !view.approved_proposal_id;
  $("personal").title = view.approved_proposal_id
    ? "본인 비공개 상담"
    : "공동 계획 전원 찬성 후 시작합니다.";
  const chat = $("chat"),
    nearEnd = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 100;
  chat.replaceChildren();
  for (const e of view.events) bubble(e, chat);
  if (changed || nearEnd) chat.scrollTop = chat.scrollHeight;
  if (changed) {
    $("text").value =
      localStorage.getItem(KEY + ":groupDraft:" + view.group_id) || "";
    personal = null;
    $("usage").textContent = "이 모임에서 선택한 실행이 없습니다.";
    $("response").textContent = "아직 실행하지 않음";
    $("requestJson").textContent = "아직 실행하지 않음";
  }
  if (view.last_run) {
    $("requestJson").textContent = JSON.stringify(
      view.last_run.request,
      null,
      2,
    );
    used(view.last_run.response);
  }
  refreshGroups();
}
async function refreshGroups() {
  try {
    const list = await api("/api/groups");
    $("groups").replaceChildren(
      ...list
        .filter(
          (r) => $("showValidation").checked || r.purpose !== "VALIDATION",
        )
        .map((r) =>
          button(
            r.title,
            async () => {
              if (busy) return;
              closePrivate();
              render(await api("/api/groups/" + r.id));
            },
            "room" + (r.id === group?.group_id ? " selected" : ""),
          ),
        ),
    );
  } catch {}
}
function nameRow(value = "") {
  const r = node("div");
  r.className = "nameRow";
  const input = node("input");
  input.placeholder = "닉네임";
  input.value = value;
  input.required = true;
  r.append(
    input,
    button("삭제", () => r.remove()),
  );
  $("names").append(r);
}
function setup() {
  $("names").replaceChildren();
  nameRow("나");
  $("setup").showModal();
}
$("newGroup").onclick = $("start").onclick = setup;
$("addName").onclick = () => nameRow();
$("create").onsubmit = async (e) => {
  e.preventDefault();
  const inputs = [...$("names").querySelectorAll("input")];
  if (!inputs.length) return;
  try {
    const view = await api("/api/groups", {
      title: $("title").value.trim(),
      members: inputs.map((i) => ({
        member_id: uuid(),
        nickname: i.value.trim(),
      })),
    });
    $("setup").close();
    render(view);
  } catch (e) {
    alert(e.message);
  }
};
$("addMember").onclick = () => $("memberDialog").showModal();
$("memberForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    render(
      await api(endpoint("/members"), {
        member: { member_id: uuid(), nickname: $("nickname").value.trim() },
        action_id: uuid(),
      }),
    );
    $("nickname").value = "";
    $("memberDialog").close();
  } catch (e) {
    notice(e.message, true);
  }
};
$("send").onsubmit = async (e) => {
  e.preventDefault();
  if (busy || !group) return;
  const text = $("text").value.trim();
  if (!text) return;
  const key = KEY + ":message:" + group.group_id;
  let pending = JSON.parse(localStorage.getItem(key) || "null");
  if (!pending || pending.text !== text || pending.member_id !== actor())
    pending = { text, member_id: actor(), message_id: uuid() };
  localStorage.setItem(key, JSON.stringify(pending));
  try {
    render(await api(endpoint("/messages"), pending));
    $("text").value = "";
    localStorage.removeItem(key);
    localStorage.removeItem(KEY + ":groupDraft:" + group.group_id);
    if (/@ai\b/i.test(text)) await runAI("COACH");
  } catch (e) {
    notice(e.message, true);
  }
};
$("text").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("send").requestSubmit();
  }
};
$("coach").onclick = () => runAI("COACH");
$("propose").onclick = () => runAI("PROPOSE");
async function castVote(agree) {
  try {
    render(
      await api(endpoint("/proposals/" + group.proposal.id + "/vote"), {
        member_id: actor(),
        agree,
        issue: agree ? "" : $("issue").value,
        reason: agree ? "" : $("reason").value.trim(),
        action_id: uuid(),
      }),
    );
    $("voteDialog").close();
    $("reason").value = "";
  } catch (e) {
    notice(e.message, true);
  }
}
$("reject").onsubmit = (e) => {
  e.preventDefault();
  castVote(false);
};
function field(parent, label, value, type = "text") {
  const l = node("label", label),
    i = node(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") i.type = type;
  i.value = Array.isArray(value) ? value.join("\n") : (value ?? "");
  l.append(i);
  parent.append(l);
  return i;
}
let editReaders = {},
  personalEditing = null;
function editProposal(p) {
  $("editorTitle").textContent = "공동 계획 직접 수정";
  $("editorHint").textContent =
    "새 버전을 게시하고 전원이 다시 투표합니다. AI 호출 없음.";
  $("editorError").textContent = "";
  personalEditing = null;
  editing = structuredClone(p);
  editReaders = {};
  const f = $("editFields");
  f.replaceChildren();
  for (const [k, label, type] of [
    ["title", "공동 목표", "text"],
    ["start_date", "시작일", "date"],
    ["end_date", "마감일", "date"],
    ["completion_criteria", "완료 기준 · 줄마다 하나", "textarea"],
    ["execution_method", "수행 방법", "textarea"],
    ["verification_method", "인증 방법", "text"],
  ])
    editReaders[k] = field(f, label, editing.goal[k], type);
  for (const m of group.members)
    editReaders["role:" + m.member_id] = field(
      f,
      m.nickname + " 담당",
      editing.goal.roles[m.member_id],
    );
  editing.tasks.forEach((t, i) => {
    const d = node("fieldset");
    d.append(node("legend", "작업 " + (i + 1)));
    for (const [k, label, type] of [
      ["title", "작업명", "text"],
      ["deliverable", "결과물", "textarea"],
      ["scheduled_date", "수행 예정일 (선택)", "date"],
      ["due_date", "기한", "date"],
      ["estimated_minutes", "예상 분", "number"],
      ["completion_criteria", "완료 기준", "textarea"],
    ])
      editReaders["task:" + i + ":" + k] = field(d, label, t[k], type);
    f.append(d);
  });
  $("editor").showModal();
}
$("edit").onsubmit = async (e) => {
  e.preventDefault();
  if (personalEditing) {
    await savePersonalEdit();
    return;
  }
  for (const [k, i] of Object.entries(editReaders)) {
    const v = i.value.trim();
    if (k.startsWith("role:")) editing.goal.roles[k.slice(5)] = v;
    else if (k.startsWith("task:")) {
      const [, idx, prop] = k.split(":");
      editing.tasks[idx][prop] =
        prop === "completion_criteria"
          ? v.split("\n").filter(Boolean)
          : prop === "estimated_minutes"
            ? Number(v)
            : prop === "scheduled_date"
              ? v || null
              : v;
    } else
      editing.goal[k] =
        k === "completion_criteria" ? v.split("\n").filter(Boolean) : v;
  }
  try {
    render(
      await api(endpoint("/proposal-edit"), {
        proposal: editing,
        member_id: actor(),
        action_id: uuid(),
        expected_revision: group.revision,
      }),
    );
    $("editor").close();
  } catch (e) {
    notice(e.message, true);
  }
};
function closePrivate() {
  if (owner && group)
    localStorage.setItem(
      KEY + ":privateDraft:" + group.group_id + ":" + owner,
      $("privateText").value,
    );
  privateEpoch++;
  personal = null;
  owner = null;
  $("privateDialog").close();
  for (const id of [
    "privateChat",
    "conditions",
    "question",
    "personalPlans",
    "personalAdvice",
    "privateDetails",
  ])
    $(id).replaceChildren();
  $("privateText").value = "";
  $("privateResponse").textContent = "아직 실행하지 않음";
  $("privateStatus").textContent = "";
}
$("personal").onclick = async () => {
  if (busy) return;
  owner = actor();
  const epoch = ++privateEpoch;
  $("privateTitle").textContent =
    group.members.find((m) => m.member_id === owner).nickname +
    "님의 기여 계획";
  $("privateStatus").textContent = "저장된 상담을 불러오고 있어요…";
  $("privateContext").replaceChildren();
  $("question").replaceChildren();
  $("privateDialog").showModal();
  try {
    const v = await api(endpoint("/personal/" + encodeURIComponent(owner)));
    if (epoch !== privateEpoch) return;
    personal = v;
    $("privateStatus").textContent =
      "아래 답변을 누르면 바로 이어집니다. 직접 입력은 선택 사항이에요.";
    $("privateText").value =
      localStorage.getItem(
        KEY + ":privateDraft:" + group.group_id + ":" + owner,
      ) || "";
    renderPersonal();
  } catch (e) {
    if (epoch === privateEpoch) $("privateStatus").textContent = e.message;
  }
};
$("actor").onchange = () => {
  if (group) localStorage.setItem(KEY + ":actor:" + group.group_id, actor());
  closePrivate();
};
const labels = {
  level: "현재 수준",
  weekly_minutes: "주간 가능 시간(분)",
  scope: "기여 범위",
  progress: "현재 진척",
  tools: "도구·자료",
  preferred_times: "선호 시간",
  available_weekdays: "확인된 가능 요일",
  constraints: "개인 제약",
  support: "필요한 도움",
};
function renderQuestion() {
  const box = $("question");
  box.replaceChildren();
  const q = personal?.state.question;
  if (!q) {
    box.className = "";
    return;
  }
  box.className = "question";
  box.append(node("strong", q.text), node("p", q.reason));
  for (const o of q.options) {
    const b = button(o.label, () =>
      privateRun({
        answer: { question_id: q.question_id, option_id: o.option_id },
        message_id: uuid(),
      }),
    );
    b.disabled = busy;
    box.append(b);
  }
  box.append(
    node(
      "p",
      busy
        ? busyAction === "PLAN"
          ? "확인한 조건으로 개인 계획을 작성하고 있어요…"
          : "선택을 받았어요. AI가 다음 안내를 준비하고 있어요…"
        : "버튼 하나를 누르세요. 아래에서 직접 답해도 돼요.",
    ),
  );
  if (busy) box.setAttribute("aria-busy", "true");
  else box.removeAttribute("aria-busy");
}
function renderPersonal() {
  if (!personal) return;
  const advice = [...personal.events]
    .reverse()
    .find((e) => e.kind === "AI_REPLY" && !e.body.stale);
  $("personalSummary").open = !personal.state.question;
  $("personalAdvice").textContent = summaryText(
    advice?.body.summary,
    advice?.body.text || "",
  );
  $("privateDetails").replaceChildren();
  if (advice?.body.details) {
    const details = node("details");
    details.append(
      node("summary", "개인 계획의 판단 근거·대안"),
      coachText(advice.body.details, false),
    );
    $("privateDetails").append(details);
  }
  if (personal.last_run) {
    $("privateResponse").textContent = JSON.stringify(
      personal.last_run,
      null,
      2,
    );
  }
  $("privateContext").replaceChildren(
    node("h3", personal.goal?.title || "공동 계획 확인 필요"),
    node(
      "p",
      personal.goal
        ? `${personal.goal.start_date} — ${personal.goal.end_date} · 내 역할: ${personal.goal.roles[owner]}`
        : "",
    ),
  );
  $("privateChat").replaceChildren();
  for (const e of personal.events) bubble(e, $("privateChat"), true);
  $("privateChat").scrollTop = $("privateChat").scrollHeight;
  const conditions = $("conditions");
  conditions.className = "conditions";
  conditions.replaceChildren();
  for (const [k, f] of Object.entries(personal.state.fields)) {
    const d = node(
      "span",
      `${labels[k] || k} · ${f.status === "KNOWN" ? (Array.isArray(f.value) ? (k === "available_weekdays" ? f.value.map((d) => ["월", "화", "수", "목", "금", "토", "일"][d]).join(", ") : f.value.join(", ")) || "없음" : f.value) : { UNKNOWN: "모름", SKIPPED: "건너뜀", SUGGESTED: "AI 추천" }[f.status]}`,
    );
    d.className = "condition";
    d.append(button("수정", () => editCondition(k, f)));
    conditions.append(d);
  }
  renderQuestion();
  $("beginPrivate").hidden = !!personal.state.question;
  $("makePlan").hidden = !["level", "weekly_minutes", "scope"].every(
    (k) => personal.state.fields[k]?.status === "KNOWN",
  );
  $("privateForm").querySelector("button").textContent = "입력한 답변 보내기";
  const list = $("personalPlans");
  list.replaceChildren();
  const p = personal.plans.find((p) => p.id === personal.current_plan_id);
  if (!p) return;
  $("privateContext").append(
    node(
      "p",
      p.stale
        ? "기존 개인 계획은 조건이 바뀌어 재검토가 필요합니다."
        : "개인 계획 초안이 준비됐습니다. 공개 요약을 검토한 뒤 본인이 공유를 결정하세요.",
    ),
    button("개인 계획 초안 검토하기", () =>
      $("personalPlans").scrollIntoView({ block: "start", behavior: "smooth" }),
    ),
  );
  const data = p.body,
    c = node("article");
  c.className = "privatePlan";
  c.append(
    node("span", p.stale ? "이전 입력 기준 · 재검토 필요" : "개인 계획 초안"),
    node("h3", data.contribution),
    node("p", data.deliverable),
  );
  for (const t of data.tasks) {
    const d = node("div");
    d.className = "task";
    d.append(
      node("strong", t.title),
      node(
        "p",
        `${t.scheduled_date ? "작업 " + t.scheduled_date + " · " : ""}기한 ${t.due_date} · ${t.estimated_minutes}분 · ${t.difficulty}`,
      ),
      node("p", t.completion_criteria.join(" · ")),
    );
    c.append(d);
  }
  const preview = node("div");
  preview.className = "publicPreview";
  preview.append(
    node("h4", "모임에 공개할 요약"),
    node("p", data.contribution),
    node("p", data.deliverable),
  );
  for (const t of data.tasks)
    preview.append(node("p", t.due_date + " · " + t.title));
  preview.append(
    node("small", "수준·가능 시간·개인 제약·대화 원문은 공유하지 않습니다."),
  );
  c.append(preview);
  c.append(button("개인 계획 직접 수정", () => editPersonalPlan(p)));
  const publish = button(
    "이 계획으로 진행하고 모임에 알리기",
    async () => {
      if (!(await confirmPublicShare(data))) return;
      try {
        const value = await api(endpoint("/personal/" + owner + "/publish"), {
          plan_id: p.id,
          action_id: uuid(),
        });
        render(value.group);
        personal = value.personal;
        renderPersonal();
        $("privateStatus").textContent =
          "본인 확인과 모임 공유가 PostgreSQL에 저장됐습니다. AI 호출 없음.";
      } catch (e) {
        $("privateStatus").textContent = e.message;
      }
    },
    "primary",
  );
  publish.disabled = p.stale;
  c.append(publish);
  list.append(c);
}
async function privateRun(params = {}, action = "TALK") {
  if (!personal || busy) return;
  const mid = owner;
  const q = personal.state.question;
  const selected = params.answer
    ? q?.options.find((o) => o.option_id === params.answer.option_id)?.label
    : params.text;
  if (selected) {
    const item = node("article");
    item.className = "message pendingAnswer";
    item.append(node("small", "나 · 전송 중"), node("div", selected));
    $("privateChat").append(item);
    $("privateChat").scrollTop = $("privateChat").scrollHeight;
  }
  const value = await runAI(action, params, mid);
  if (
    value &&
    owner === mid &&
    value.result.persistence?.state === "COMPLETE" &&
    params.text &&
    $("privateText").value.trim() === params.text
  ) {
    $("privateText").value = "";
    localStorage.removeItem(
      KEY + ":privateDraft:" + group.group_id + ":" + mid,
    );
  }
}
$("privateForm").onsubmit = (e) => {
  e.preventDefault();
  const text = $("privateText").value.trim();
  if (text) privateRun({ text, message_id: uuid() });
};
$("beginPrivate").onclick = () => privateRun();
$("makePlan").onclick = () => {
  const text = $("privateText").value.trim();
  if (text) {
    $("privateStatus").textContent =
      "작성한 답변을 먼저 반영합니다. 확인된 조건을 보고 개인 계획을 만들어주세요.";
    privateRun({ text, message_id: uuid() }, "TALK");
  } else privateRun({}, "PLAN");
};
$("examples").onclick = async () => {
  if (!scenarios.length) scenarios = await api("/api/scenarios");
  filterScenarios();
  $("library").showModal();
};
function filterScenarios() {
  const term = $("search").value.toLowerCase();
  $("scenarioList").replaceChildren(
    ...scenarios
      .filter((s) => (s.title + " " + s.domain).toLowerCase().includes(term))
      .map((s) => {
        const d = node("div");
        d.className = "scenario";
        d.append(
          node("strong", (s.kind === "BASE" ? "기본 · " : "변형 · ") + s.title),
          node("p", s.summary),
        );
        d.onclick = async () => {
          selectedScenario = await api(
            "/api/scenarios/" + encodeURIComponent(s.case_id),
          );
          const p = $("scenarioPreview");
          p.replaceChildren(node("h3", selectedScenario.title));
          for (const m of selectedScenario.input.conversation)
            p.append(
              node(
                "p",
                selectedScenario.input.members.find(
                  (x) => x.member_id === m.speaker_id,
                )?.nickname +
                  ": " +
                  m.text,
              ),
            );
          p.append(
            button("이 대화로 새 예제 모임 만들기 · AI 호출 없음", async () => {
              closePrivate();
              render(
                await api(
                  "/api/scenarios/" + encodeURIComponent(s.case_id) + "/load",
                  {},
                ),
              );
              $("library").close();
            }),
          );
        };
        return d;
      }),
  );
}
$("search").oninput = filterScenarios;
$("showValidation").onchange = refreshGroups;
document.querySelectorAll("[data-close]").forEach(
  (b) =>
    (b.onclick = () => {
      if (b.dataset.close === "privateDialog") closePrivate();
      else $(b.dataset.close).close();
    }),
);
async function init() {
  try {
    const c = await api("/api/config");
    $("connection").textContent =
      `${c.configured ? "LIVE · Timely GPT" : "API 키 설정 필요"} · ${c.model} · v${c.version}`;
    await refreshGroups();
    const id =
      new URLSearchParams(location.search).get("group") ||
      localStorage.getItem(KEY + ":group");
    if (id) render(await api("/api/groups/" + encodeURIComponent(id)));
  } catch (e) {
    notice(e.message, true);
  }
}
init();
setInterval(async () => {
  if (!group || busy || document.hidden) return;
  try {
    const v = await api(endpoint());
    if (v.revision !== group.revision) render(v);
  } catch {
    $("saved").textContent = "연결 확인 필요 · 저장 완료 아님";
  }
}, 3000);

let conditionKey = null;
function editCondition(key, f) {
  conditionKey = key;
  $("conditionTitle").textContent = labels[key] + " · 본인 확인";
  $("conditionState").value = f.status === "SUGGESTED" ? "KNOWN" : f.status;
  $("conditionValue").value = Array.isArray(f.value)
    ? f.value.join("\n")
    : (f.value ?? "");
  $("conditionDialog").showModal();
}
$("conditionForm").onsubmit = async (e) => {
  e.preventDefault();
  const status = $("conditionState").value;
  const raw = $("conditionValue").value.trim();
  const value =
    status !== "KNOWN"
      ? null
      : conditionKey === "weekly_minutes"
        ? Number(raw)
        : conditionKey === "available_weekdays"
          ? raw
              .split(/[,\s]+/)
              .filter(Boolean)
              .map(Number)
          : ["level", "scope", "progress"].includes(conditionKey)
            ? raw
            : raw.split("\n").filter(Boolean);
  try {
    personal = await api(endpoint("/personal/" + owner + "/conditions"), {
      updates: { [conditionKey]: { value, status } },
      action_id: uuid(),
    });
    $("conditionDialog").close();
    renderPersonal();
    $("privateStatus").textContent =
      "개인 조건을 수정했습니다. 기존 계획은 재검토가 필요합니다. AI 호출 없음.";
  } catch (e) {
    $("privateStatus").textContent = e.message;
  }
};
function editPersonalPlan(p) {
  $("editorTitle").textContent = "내 계획 직접 수정";
  $("editorHint").textContent =
    "새 개인 계획 버전으로 저장합니다. 모임 공유는 공개 요약 확인 후 별도로 진행합니다. AI 호출 없음.";
  $("editorError").textContent = "";
  personalEditing = p;
  editReaders = {};
  const f = $("editFields");
  f.replaceChildren();
  for (const [k, label, type] of [
    ["contribution", "기여 목표", "text"],
    ["deliverable", "담당 결과물", "textarea"],
    ["completion_criteria", "완료 기준", "textarea"],
  ])
    editReaders[k] = field(f, label, p.body[k], type);
  p.body.tasks.forEach((t, i) => {
    const d = node("fieldset");
    d.append(node("legend", "내 작업 " + (i + 1)));
    for (const [k, label, type] of [
      ["title", "작업명", "text"],
      ["deliverable", "결과물", "textarea"],
      ["scheduled_date", "수행 예정일 (선택)", "date"],
      ["due_date", "기한", "date"],
      ["estimated_minutes", "예상 분", "number"],
      ["completion_criteria", "완료 기준", "textarea"],
    ])
      editReaders["task:" + i + ":" + k] = field(d, label, t[k], type);
    f.append(d);
  });
  $("editor").showModal();
}
async function savePersonalEdit() {
  const p = structuredClone(personalEditing.body);
  for (const [k, input] of Object.entries(editReaders)) {
    const value = input.value.trim();
    if (k.startsWith("task:")) {
      const [, index, prop] = k.split(":");
      p.tasks[index][prop] =
        prop === "completion_criteria"
          ? value.split("\n").filter(Boolean)
          : prop === "estimated_minutes"
            ? Number(value)
            : prop === "scheduled_date"
              ? value || null
              : value;
    } else
      p[k] =
        k === "completion_criteria" ? value.split("\n").filter(Boolean) : value;
  }
  try {
    personal = await api(endpoint("/personal/" + owner + "/edit"), {
      plan_id: personalEditing.id,
      plan: p,
      action_id: uuid(),
    });
    $("editor").close();
    personalEditing = null;
    renderPersonal();
    $("privateStatus").textContent =
      "새 개인 계획 버전으로 저장했습니다. AI 호출 없음.";
  } catch (e) {
    $("privateStatus").textContent = e.message;
    $("editorError").textContent = e.message;
  }
}

$("privateText").addEventListener("input", () => {
  if (owner && group)
    localStorage.setItem(
      KEY + ":privateDraft:" + group.group_id + ":" + owner,
      $("privateText").value,
    );
});
$("text").addEventListener("input", () => {
  if (group)
    localStorage.setItem(
      KEY + ":groupDraft:" + group.group_id,
      $("text").value,
    );
});

// A second explicit confirmation remains mandatory. This in-page dialog shows
// only the public summary and works in an embedded frame without native alerts.
let shareConfirmationResolve = null;
function confirmPublicShare(plan) {
  const preview = $("shareConfirmPreview");
  preview.replaceChildren(
    node("h3", plan.contribution),
    node("p", plan.deliverable),
  );
  for (const task of plan.tasks)
    preview.append(node("p", task.due_date + " · " + task.title));
  $("shareConfirmDialog").showModal();
  return new Promise((resolve) => {
    shareConfirmationResolve = resolve;
  });
}
function finishShareConfirmation(confirmed) {
  const resolve = shareConfirmationResolve;
  shareConfirmationResolve = null;
  $("shareConfirmDialog").close();
  if (resolve) resolve(confirmed);
}
$("shareConfirmForm").onsubmit = (event) => {
  event.preventDefault();
  finishShareConfirmation(true);
};
$("shareConfirmDialog").addEventListener("close", () => {
  if (shareConfirmationResolve) finishShareConfirmation(false);
});
