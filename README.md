# FCL · Fabrique Content Lab

> 设计师品牌 AIGC 内容生产中台：**批量生成 → 硬规则质检 → 四维打分 → 人工审核 → 成本看板**。
>
> 个人项目 · 借助 AI 编程工具（Codex / DSH）实现，本人负责需求拆解、技术选型、提示词与评测设计，并逐文件读懂。

## 3 分钟跑起来（不需要任何 API Key）

```bash
cd project
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python scripts/seed.py            # 造 12 个商品
python -m uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000> 即可。

默认使用 **mock 模型**：离线、免费、可复现，用来演示完整链路（含失败重试、JSON 修复、违禁词拦截）。

接入真实模型（DeepSeek / 通义 / 本地 Ollama 均可）：

```bash
set FCL_PROVIDER=openai_compat
set FCL_LLM_BASE_URL=https://api.deepseek.com/v1
set FCL_LLM_API_KEY=sk-xxxx
set FCL_LLM_MODEL=deepseek-chat
```

## 目录

```
app/
  main.py            FastAPI 入口（路由装配 + 生命周期 + 静态页）
  models.py          5 张表：products / runs / contents / eval_records / events
  api/               products / runs / dashboard
  providers/         LLM 抽象层：mock（离线） / openai_compat（真实）
  services/
    pipeline.py      生成编排：缓存 → RAG → Writer → Critic → Judge → 重写
    queue.py         asyncio 队列 + 并发上限 + 超时重试 + 幂等 + 重启补偿
    quality.py       违禁词规则引擎 + LLM-as-Judge 四维打分
    rag.py           品牌规范分块与检索（向量优先，关键词兜底）
    cache.py         TTL + LRU + 单飞（防击穿）+ 可选 Redis
    ratelimit.py     令牌桶
    metrics.py       看板指标聚合
  static/            后台单页（看板 / 生产台 / 审核队列 / 系统状态）
scripts/
  seed.py            商品种子数据
  demo_batch.py      批量压测，输出真实指标到 data/metrics.json
  smoke_test.py      端到端冒烟测试
tests/test_core.py   规则、限流、缓存、检索、JSON 修复的单测
```

## 实测指标（`python scripts/demo_batch.py --count 100 --passes 2`）

| 指标 | 冷启动（100 条） | 复跑（命中缓存） |
|---|---|---|
| 生成条数 | 100 | 100 |
| 端到端耗时 | 40.4s（受 120 次/分钟限流） | 49.8s* |
| 单条 P50 / P95 | 138ms / 504ms | ~0ms / ~0ms |
| 一次通过率 | 73.0% | 73.0% |
| 规则拦截率 | 26.0% | 26.0% |
| 平均分 | 84.1 | 84.1 |
| token 消耗 | 86,946 | 0 |
| 估算成本 | ¥0.1172 | ¥0 |

\* 第二轮的「端到端耗时」把两轮一起算进去了，单条延迟才是真实的（~0ms）。指标文件见 `data/metrics.json`。

> 这些数字是 mock 模型下的工程指标，**不是**真实大模型的业务效果；接入真实 API 后需要重跑。

## 设计取舍（面试可讲）

- **为什么用队列**：HTTP 请求等不了 100 条生成；并发不限会打挂上游；失败要能重试和断点续跑。
- **为什么规则 + 模型两层质检**：广告法红线必须确定性拦截，调性判断交给模型，最终由人拍板。
- **为什么缓存 key 带提示词版本**：提示词一改，旧缓存必须失效，否则会拿旧结果骗人。
- **为什么能降级**：Redis 不可用退化为内存、embedding 不可用退化为关键词、真实模型不可用退化为 mock——演示环境永远跑得起来。
- **边界**：单机队列，进程重启依赖数据库补偿；没做鉴权、多租户、真实向量库；上线前要换 Celery + Redis + MySQL + 鉴权。

## 测试

```bash
.venv\Scripts\python.exe -m pytest -q
```

## 许可

仅用于个人求职展示。
