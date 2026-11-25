# 从 0 到 1 施工手册

> 按这份文档，一个人可以在 **4 天**（每天 6 小时）内做出这个项目。
> 原则：**每个阶段结束都必须有能跑的东西**，不做完一整个模块才开始下一个。
>
> 时间分配：D1 骨架 + 检索 + 回答；D2 工单 + 看板；D3 飞书 + 大模型；D4 测试 + 文档 + 发布。

---

## 阶段 0：准备环境（30 分钟）

### 0.1 装环境

```bash
mkdir auto-4s-ai-service && cd auto-4s-ai-service
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install fastapi uvicorn pytest httpx
```

验证：`python -c "import fastapi, pytest; print('ok')"`

### 0.2 建目录（一开始就分好，后面不用挪）

```
app/        后端模块
data/       faq.json、app.db
static/     index.html、app.js、style.css
tests/      pytest
docs/       文档
```

### 0.3 先写需求，不写代码

用一页纸回答四个问题，写进 `docs/requirements.md`：

1. **给谁用**：4S 店客户、客服、客服主管；
2. **解决什么**：重复咨询、乱答风险、答不上来流失；
3. **做哪两类场景**：售前咨询 + 售后维保（只做 3 款车，不做支付）；
4. **怎么算成功**：自动解决率 ≥ 70%、转人工率 ≤ 20%、平均首响 ≤ 30 分钟。

> ⚠️ 这一步省了，后面一定返工：字段会反复改、指标会算不清。

---

## 阶段 1：数据模型（1 小时）

### 1.1 建六张表

`app/db.py`，用 SQLite 原生 `sqlite3`，不用 ORM：

| 表 | 作用 | 关键字段 |
|---|---|---|
| `faq` | 知识条目 | id, category, question, variants(JSON), models(JSON), scene, **phenomenon**, answer, source, owner, updated_at, status, tags(JSON), priority, hits |
| `faq_fts` | 检索索引 | FTS5 虚表，`tokenize='trigram'`，只索引「生效」条目 |
| `sessions` | 会话 | id, customer, channel, model, scene, status, created_at |
| `messages` | 消息 + AI 决策 | role, text, intent, emotion, confidence, status, faq_ids, **decision(JSON)** |
| `tickets` | 工单 | 16 个业务字段 + sla_first_due / sla_resolve_due |
| `events` | 审计 | kind, ref, payload, created_at |

### 1.2 三个必须提前定的细节

1. **时间统一存 ISO 字符串带时区**（`2026-11-26T03:30:00+08:00`），不然排序和 SLA 会错；
2. **JSON 字段存字符串**，读的时候 `json.loads`，SQLite 不支持数组；
3. **加 `init_db()` 做幂等建表** + 老库加列的迁移：

```python
def init_db(conn):
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(faq)")}
    if "phenomenon" not in cols:
        conn.execute("ALTER TABLE faq ADD COLUMN phenomenon TEXT NOT NULL DEFAULT ''")
    conn.commit()
```

### 1.3 验收

```bash
python -c "from app import db; c=db.connect('data/test.db'); db.init_db(c); print('ok')"
```

---

## 阶段 2：知识库内容（1 天，最重要）

### 2.1 先写 10 条，跑通再说

不要一上来写 69 条。先写 10 条覆盖最常见问题（续航、充电、首保、价格、投诉），验证检索逻辑，再批量补。

### 2.2 每条 FAQ 的字段（13 个，缺一不可）

```json
{
  "id": "FAQ-029",
  "category": "售后",
  "question": "新车多久做首保",
  "variants": ["首保时间", "第一次保养", "多少公里保养"],
  "models": ["全车型"],
  "scene": "售后保养",
  "phenomenon": "保养",
  "answer": "首保为购车后 6 个月或 5000 公里，以先到为准，首保免费。",
  "source": "保养手册 P6",
  "owner": "售后技术组-李工",
  "updated_at": "2026-11-12",
  "status": "生效",
  "tags": ["保养", "首保", "周期"],
  "priority": "P1"
}
```

### 2.3 写内容的四条规矩

1. **问题用客户原话**，不用内部术语（「首保时间」而不是「首次保养作业周期」）；
2. **同义问法 3—6 条**，别堆 10 条近义词（会稀释打分）；
3. **答案 1—3 句**，先结论后条件，数字必须有 `source`；
4. **涉及价格/质保/退订**的答案，不写承诺，标 `高风险` 标签。

### 2.4 验收

```bash
python -c "import json; d=json.load(open('data/faq.json',encoding='utf-8')); print(len(d), '条')"
```

写一个 10 行脚本检查：id 不重复、必填字段不为空、status 合法。

---

## 阶段 3：检索与打分（半天，项目的心脏）

### 3.1 思路：召回 + 打分，两步

```
问题 → FTS5 trigram 召回候选 → 对每条候选算「覆盖度」→ 排序取 Top3
```

### 3.2 打分公式（照抄即可）

```
confidence = 0.55 × 标准问题覆盖
           + 0.25 × 同义问法覆盖
           + 0.08 × 标签覆盖
           + 0.05 × 答案覆盖
           + 车型加权（命中同车型 +0.14 / 全车型 +0.07 / 错车型 -0.12）
```

其中「覆盖」= 问题里有多少片段出现在该字段：

```
覆盖 = 0.7 × 二字片命中率 + 0.3 × 单字命中率
```

为什么要单字：容忍语序变化（「可以上门试驾吗」vs「上门试驾可以吗」）。

### 3.3 三个必须做的修正

1. **完全等于标准问题 → 0.90 起**；**完全等于某条同义问法 → 0.86 起**；
2. **3 字以内问题封顶 0.60**（「多少钱」「首保」不该直接答），带车型的不封顶；
3. **车型单独处理**：从问题里剔掉车型词再算片段，否则「X3 首付」这种会把分母撑大。

### 3.4 阈值

| 置信度 | 动作 |
|---|---|
| ≥ 0.62 | 直接回答 |
| 0.30—0.62 | 追问车型/现象 |
| < 0.30 | 不猜，转人工 |

### 3.5 验收（这一步必须用真实问题测）

```bash
python - <<'PY'
from app import db, kb, config
c = db.connect('data/app.db'); kb.seed_from_file(c, config.FAQ_FILE)
for q in ["S7 续航多少", "多少钱", "维保周期", "你们公司股票代码是多少"]:
    hits = kb.search(c, q)
    print(q, "->", hits[0]["id"], hits[0]["confidence"])
PY
```

预期：S7 续航 ≈0.93；多少钱 ≈0.60（追问）；维保周期 ≈0.93；股票代码 <0.2。

> ⚠️ 坑 1：FTS5 的 trigram 对 3 字以下查询召回不到 → 一定要留「候选不足 3 条时全量打分」的兜底。
> ⚠️ 坑 2：同义词扩展如果直接拼进查询，会把分母变大、置信度反而下降 → 用 `max(原句分, 扩展句分)`。

---

## 阶段 4：NLU 规则（半天）

`app/nlu.py` 一张规则表搞定，不要上模型：

```python
INTENT_RULES = [
    ("投诉", "边界", ["投诉", "曝光", "12315", "起诉"]),
    ("退订退款", "边界", ["退订", "退车", "退款"]),
    ("价格承诺", "边界", ["底价", "最低价", "便宜点"]),
    ("保养", "售后", ["保养", "首保", "维保"]),
    # ...
]
```

按顺序匹配，**越危险的意图放越前面**。

额外输出 5 个字段：

1. `models`：扫车型别名（S7/X3/L9）；
2. `emotion`：负面词表 / 犹豫词表 → 平稳、犹豫、负面；
3. `risk`：高风险词 + 强制转人工意图 → `level=high`；
4. `missing`：缺车型 / 缺故障现象 → 决定要不要追问；
5. `symptom`：现象词表（响、抖、黑屏、跳枪…）。

### 验收

```bash
python -c "from app import nlu; print(nlu.analyze('我要投诉门店服务态度'))"
```

预期：intent=投诉、risk.level=high。

---

## 阶段 5：决策与分流（半天）

`app/answer.py` 一个 `decide()` 函数，按优先级判断：

```python
if risk == "high":            status = "handoff"     # 安全第一
elif not hits:                status = "no_hit"      # 不猜
elif conflict:                status = "handoff"     # 两条知识打架
elif conf < 0.62:             status = "clarify"     # 追问
elif missing and conf < 0.80: status = "clarify"     # 缺关键信息
else:                         status = "answered"
```

四个状态的答复文案完全不同：

| 状态 | 回复 |
|---|---|
| answered | 答案 + 「依据：来源（负责人，更新时间）」 |
| clarify | 「我理解您想咨询「…」，麻烦补充：车型？现象？」 |
| handoff | 「需要人工同事跟进，我先建工单」 |
| no_hit | 「知识库暂时没有可靠答案，为避免答错先转人工」 |

决策结果整份存进 `messages.decision`（JSON），前端决策面板直接回放。

### 验收

```bash
python - <<'PY'
from app import db, answer, config
c = db.connect('data/app.db')
for q in ["S7 续航多少", "多少钱", "给我个底价", "你们公司股票代码是多少"]:
    d = answer.decide(c, q)
    print(f'{q} -> {d["status"]:<9} {d["confidence"]}')
PY
```

预期：answered / clarify / handoff / no_hit 各一条。

---

## 阶段 6：服务层（2 小时）

`app/service.py`，把「发消息」封装成一个函数，API 和演示数据都调它：

```python
def add_turn(conn, session_id, text):
    decision = answer.decide(conn, text)
    user_id = insert_message(role="user", ...)
    ai_id   = insert_message(role="ai", text=decision["answer"],
                             status=map_status(decision["status"]), decision=decision)
    if decision["status"] in ("handoff", "no_hit"):
        ticket = tickets.create_ticket(...)   # 自动建单
    return {...}
```

状态映射：`answered→pending`、`clarify→clarify`、`handoff→handed_off`、`no_hit→no_hit`。

再加两个动作：

- `review_message(action)`: accepted / edited / rejected / handed_off；驳回和转人工自动建单；
- `feedback(result)`: solved / unsolved / wrong；未解决和错误自动建单。

> ⚠️ 坑：同一个 AI 消息重复驳回会建出两张工单 → 建单前先按 `message_id` 查一次。

---

## 阶段 7：REST API（2 小时）

`app/api.py`，FastAPI 路由，18 个接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 健康检查（faq/sessions/tickets 数量） |
| GET | `/api/config` | 阈值、车型词表 |
| POST | `/api/chat` | 发消息，返回决策 |
| GET | `/api/sessions`、`/api/sessions/{id}` | 会话列表/详情 |
| POST | `/api/messages/{id}/review` | 审核动作 |
| POST | `/api/messages/{id}/feedback` | 客户反馈 |
| GET | `/api/tickets`、`/api/tickets/{id}` | 工单列表/详情 |
| PATCH | `/api/tickets/{id}` | 状态/负责人/根因/方案/满意度 |
| POST | `/api/tickets/{id}/faq-draft` | 回写 FAQ 草稿 |
| GET | `/api/faq`（支持 `q` 搜索） | 知识库列表 |
| POST/PATCH | `/api/faq`、`/api/faq/{id}` | 新建/修改 |
| POST | `/api/faq/{id}/publish` | 审核发布 |
| GET | `/api/dashboard`、`/api/bad-cases` | 看板与 Bad Case |
| POST | `/api/demo/seed`、`/api/demo/reset` | 演示数据 |
| GET/POST | `/api/feishu/status`、`/api/feishu/sync` | 飞书 |

依赖注入 `get_conn` 每请求一个连接，`create_app(db_path)` 方便测试换库。

### 验收

```bash
uvicorn app.main:app --port 8000
curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/chat -H "Content-Type: application/json" -d '{"text":"S7 续航多少"}'
```

打开 http://localhost:8000/docs 看 Swagger，18 个接口都在。

---

## 阶段 8：前端单页（半天）

原生 HTML/CSS/JS，不装 npm。四个标签页：

1. **工作台**：三栏 = 会话列表 / 聊天 / AI 决策面板（意图、证据、四个审核按钮）；
2. **工单**：表格 + 右侧详情（SLA 倒计时、状态、根因、方案）；
3. **看板**：8 个 KPI + 6 张榜；
4. **知识库**：搜索、状态筛选、发布按钮、新增表单。

三个实现要点：

- `fetch` 封装 `api()`，非 2xx 统一 toast；
- 渲染一律 `textContent` 或转义，防 XSS；
- 用 `location.hash` 做标签页路由，方便截图和分享（`#dashboard`）。

> ⚠️ 坑：中文数字/百分比画进度条时记得乘 100 再取整，否则是 0.62 而不是 62%。

---

## 阶段 9：工单 + SLA（半天）

1. 建单时按优先级算截止时间：`P0=(15min, 4h)`、`P1=(30min, 8h)`、`P2=(2h, 48h)`；
2. 状态改「处理中」→ 打 `first_response_at`；「已解决」→ 打 `resolved_at`；
3. 详情接口返回 `sla.first_response.remaining_minutes`，负数就是超时分钟数；
4. **解决时必填根因和方案**，否则拒绝生成 FAQ 草稿。

### 验收

```bash
curl -s localhost:8000/api/tickets | python -m json.tool | head -30
```

看每条有没有 `sla` 字段、剩余分钟数对不对。

---

## 阶段 10：看板指标（半天）

`app/metrics.py` 全部实时算，不建宽表：

| 指标 | 公式 |
|---|---|
| 自动解决率 | (accepted+edited) / 已审核数 |
| 转人工率 | handed_off / AI 建议数 |
| 命中率 | 有证据且置信度≥阈值 / AI 建议数 |
| 平均首响 | avg(first_response - created) |
| 满意度 | avg(tickets.satisfaction) |
| 故障现象分布 | 按 phenomenon 分组聚合命中数 |
| Bad Case | 未命中 + 驳回 + 客户反馈错误/未解决 |

### 验收

造 10 条演示数据后，手算一遍自动解决率，和接口返回对得上。

> ⚠️ 坑：分母为 0 时要返回 0 而不是崩；`avg` 在没有数据时返回 None 而不是 0。

---

## 阶段 11：回写闭环（2 小时）

```
工单已解决 + 有 solution
  → POST /api/tickets/{id}/faq-draft   建 FAQ（status=待审核）
  → 审核 → POST /api/faq/{id}/publish  改 status=生效 + 重建 FTS
  → 下次命中
```

关键点：**待审核的条目不能进 FTS 索引**，否则 AI 会把没核对的内容说出去。

### 验收

```bash
# 1. 把一条工单改成已解决并填方案
curl -X PATCH .../api/tickets/{id} -d '{"status":"已解决","solution":"..."}'
# 2. 生成草稿
curl -X POST .../api/tickets/{id}/faq-draft
# 3. 搜索该问题 → 应该搜不到（待审核）
# 4. 发布后再搜 → 命中
```

---

## 阶段 12：飞书多维表格（半天，含权限审批）

### 12.1 建应用与权限（最容易卡住）

1. https://open.feishu.cn/app → 创建企业自建应用 → 记 App ID / App Secret；
2. 权限管理 → 开通 **`bitable:app`**；
3. **版本管理与发布 → 创建版本 → 发布**（不发布权限不生效，报 99991672）；
4. 若后台把 `bitable:app` 标为废弃，就把「多维表格」相关的权限全勾上。

### 12.2 建表

`python -m app.feishu init` 自动建「多维表格 + 会话表 + 工单表」，token 写回 `.env`。

两张表各 16 字段：

- 会话表：会话ID、客户昵称、性别、平台、客层、意图、情绪、转化状态、标签、询问商品、售价、时间、问题状态、负责人、优先级、SLA节点；
- 工单表：工单编号、原始问题、意图、车型与场景、AI召回条目、AI回答、转人工原因、优先级、负责人、处理状态、首响应时间、解决时间、根因、方案、满意度、是否建议新增FAQ。

### 12.3 写入

按业务主键去重（会话ID / 工单编号）：先查记录，存在就 PUT，不存在就 POST。

> ⚠️ 坑 1：单选字段的选项必须先建好，写不存在的选项会报错。
> ⚠️ 坑 2：日期字段要毫秒时间戳（int），传字符串会失败。
> ⚠️ 坑 3：文本字段读回来可能是富文本数组 `[{"text":"..."}]`，去重比较前要拼回字符串。

---

## 阶段 13：大模型改写（2 小时）

```
检索结果 → 提示词（只准依据资料）→ 模型改写 → 数字校验 → 通过则用，失败回退规则答案
```

三层护栏：

1. **提示词**：只准依据资料、禁止价格承诺、资料不足输出 `NEED_HANDOFF`；
2. **数字校验**：答案里的每个数字必须出现在证据里，否则判无依据；
3. **异常回退**：超时、额度、网络错误一律回退规则拼接，不报错。

配置：`.env` 里 `A4S_LLM=1` + `DEEPSEEK_API_KEY=sk-...`，验证 `python -m app.llm "S7 续航多少"`。

---

## 阶段 14：测试（半天）

按层写，别只写接口测试：

| 文件 | 测什么 |
|---|---|
| `test_kb.py` | 召回准确、短问题封顶、同义词命中、草稿不可检索、冲突检测 |
| `test_nlu.py` | 意图、车型、情绪、风险、缺信息 |
| `test_answer.py` | 四路分流各一条 + 阈值可调 + 命中数递增 |
| `test_tickets.py` | SLA 时限、首响打点、解决打点、超时判定、草稿前置条件 |
| `test_metrics.py` | 指标区间、除零保护、Bad Case、故障分布 |
| `test_api.py` | 主流程、驳回建单、404、发布后才可检索 |
| `test_feishu.py` | 16 字段映射、去重逻辑（HTTP 打桩） |
| `test_llm.py` | 数字护栏、NEED_HANDOFF、失败回退（HTTP 打桩） |

**测试必须离线**：`conftest.py` 里强制 `os.environ["A4S_LLM"]="0"`，否则本地开着大模型跑测试会真花钱。

---

## 阶段 15：文档与演示（半天）

| 文档 | 内容 |
|---|---|
| `README.md` | 一分钟看懂 + 截图 + 一键启动 + 指标口径 |
| `docs/architecture.md` | 分层、数据模型、时序、打分公式 |
| `docs/sla.md` | 优先级、计时口径、飞书字段表 |
| `docs/faq-spec.md` | 13 字段定义、同义问法写法、审核 checklist |
| `docs/sop.md` | 状态表、日/周流程、回写流程、周报模板 |
| `docs/demo-script.md` | 5 分钟演示脚本 + 高频追问 |

演示数据用**真实链路**生成（调 `service.add_turn` 再 review/feedback），不要手写死数据——看板数字才解释得清。

---

## 阶段 16：发布（1 小时）

```bash
git init && git add -A && git commit -m "feat: 初版"
git remote add origin git@github.com:<你>/<仓库>.git
git push -u origin main
```

- 仓库设 **public**（否则面试官打不开）；
- `.gitignore` 必须有：`.venv/`、`__pycache__/`、`data/app.db`、`.env`；
- 仓库 Settings 填 Description 和 Topics（`ai-customer-service`、`fastapi`、`rag`、`customer-success`）。

---

## 附：四个最容易卡住的地方

| 卡点 | 现象 | 解法 |
|---|---|---|
| FTS5 中文召回不到 | 短问题搜不到 | trigram 分词 + 候选 <3 条时全量打分 |
| 置信度虚高 | 「多少钱」也直接答 | 3 字以内封顶 0.60，带车型才放开 |
| 飞书 99991672 | 权限报错 | 开通 `bitable:app` **并发布版本** |
| 测试真花钱 | pytest 很慢且产生费用 | conftest 强制关 LLM |

## 附：里程碑验收清单

- [ ] D1 晚：`curl /api/chat` 能返回 4 种状态；
- [ ] D2 晚：工单能建、SLA 能算、看板 8 个数字对得上；
- [ ] D3 晚：飞书两张表有数据；大模型改写数字全过校验；
- [ ] D4 晚：66 个测试全绿、README 有截图、仓库 public、能 5 分钟演示。
