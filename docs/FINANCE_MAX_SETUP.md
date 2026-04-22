# Finance Max Preset

`finance_max` 是一套按财经工作流重构的 TrendRadar 预设，不是简单换几个关键词。

当前默认落地的是 `finance_digest_card` 调度模板：

- 全天采集，不中断积累数据
- 早 `08:00` 推一次晨报
- 晚 `22:00` 推一次晚报
- 飞书走 `card_digest` 摘要卡片，自动去重，最多 `10` 条
- 卡片头部会额外生成一段「两次推送间重大影响」LLM 总结
- 默认通知链路走 OpenClaw 的 `crawler` Feishu route，而不是普通飞书 webhook

目标：

- 盘前看政策和宏观预警
- 盘中抓异动、轮动和事件驱动
- 收盘后做当日深挖
- 夜间盯海外宏观窗口
- 周末输出下周 watchlist

## Included

文件位置：

- `config/presets/finance_max/config.yaml`
- `config/presets/finance_max/timeline.yaml`
- `config/presets/finance_max/frequency_words.txt`
- `config/presets/finance_max/ai_interests.txt`
- `config/presets/finance_max/custom/keyword/*.txt`
- `config/presets/finance_max/custom/ai/*.txt`
- `scripts/apply_finance_max_preset.py`

## Source Design

热榜层：

- `wallstreetcn-hot`
- `cls-hot`
- `weibo`
- `zhihu`
- `thepaper`
- `ifeng`
- `toutiao`
- `baidu`
- `bilibili-hot-search`

RSS 层：

- `https://www.sec.gov/news/pressreleases.rss`
- `https://www.sec.gov/news/speeches.rss`
- `https://www.federalreserve.gov/feeds/press_all.xml`
- `https://www.federalreserve.gov/feeds/press_monetary.xml`
- `https://www.federalreserve.gov/feeds/speeches.xml`
- `https://www.bls.gov/feed/bls_latest.rss`
- `https://www.bls.gov/feed/cpi.rss`
- `https://www.bls.gov/feed/ppi.rss`
- `https://apps.bea.gov/rss/rss.xml`
- `https://www.ecb.europa.eu/rss/press.html`
- `https://www.ecb.europa.eu/rss/statpress.html`
- `https://www.ecb.europa.eu/rss/fxref-cny.html`
- `https://www.bankofengland.co.uk/rss/news`
- `https://www.bankofengland.co.uk/rss/speeches`
- `https://www.bis.org/doclist/all_pressrels.rss`
- `https://www.bis.org/doclist/cbspeeches.rss`
- `https://www.hkex.com.hk/Services/RSS-Feeds/News-Releases?sc_lang=en`
- `https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=en`
- `https://finance.yahoo.com/news/rssindex`

说明：

- 上述 RSS 在 `2026-04-22` 已实际请求校验。
- `SEC litigation releases`、`US Treasury press releases` 这类页面当前不适合作为稳定 RSS 主源，所以没有放进主预设。
- 中国官方金融机构很多没有稳定公开 RSS，当前方案用 `财联社 / 华尔街见闻 / 澎湃 / 微博` 补充时效层。

## Timeline

默认摘要卡片模式：

- `08:00-08:30` 财经晨报：去重后的 top10 摘要
- `22:00-22:30` 财经晚报：去重后的 top10 摘要
- 其余时间：只采集，不推送

原始高频财经模式 `finance_max` 仍然保留：

工作日：

- `06:00-07:00` 隔夜收盘晨检：美股收盘、汇率、商品、VIX 快扫
- `07:00-09:00` 盘前预警：关键词模式，宏观/政策/监管优先
- `09:00-11:35` A 股早盘：关键词模式，盘中异动与轮动
- `11:35-13:15` 午间复盘：AI 模式，解释盘面变化
- `14:30-15:30` 收盘异动：关键词模式，事件驱动和风险信号
- `15:30-18:20` 公告财报监管窗：关键词模式，盯收盘后的正式披露
- `18:20-20:20` 晚间深挖：AI 模式，当日汇总
- `20:20-23:30` 海外宏观窗口：关键词模式，盯美国数据与联储信号
- `23:30-06:00` 静默积累：只采集不推送

周末：

- `06:00-07:00` 隔夜收盘晨检
- `09:30-11:30` 周末策略复盘
- `20:20-22:30` 周末海外窗口

## Apply

在仓库根目录执行：

```bash
python3 scripts/apply_finance_max_preset.py
```

脚本会：

- 备份当前 `config/` 到 `config/backups/finance_max-时间戳/`
- 应用 `finance_max` 主配置
- 保留这些运行时配置：
  - `app.timezone`
  - `notification.channels`
  - `storage.backend/local/remote/pull`
  - `ai.api_key`
  - `ai.api_base`
  - 代理配置

## Runtime Recommendation

推荐部署方式：

- `Docker / 本地 cron`，间隔 `15~30` 分钟
- 不推荐把这套财经 preset 首发跑在 `GitHub Actions` 的小时级触发上，粒度太粗

建议：

- `report.display_mode` 保持 `keyword`
- `max_news_per_keyword` 控制在 `8` 左右，避免主线被单一源刷屏
- `display.standalone` 保持开启，完整保留 `华尔街见闻 / 财联社 / 核心官方 RSS`
- 飞书如果要真正显示 interactive card，请使用标准机器人 webhook；`www.feishu.cn` 这类 flow webhook 会自动回退成精简文本摘要
- OpenClaw 路径下会直接走 `openclaw message send --card ...`，并复用 `crawler` 最近一次 Feishu reply target
- 摘要去重历史默认保存在 `output/meta/feishu_digest_history.json`
- 摘要评分表默认在 `config/feishu_digest_scoring.json`
- 两次推送间影响总结默认开启，基于上次成功推送时间到本次推送时间之间的新事件生成
- 本地 HTML 报告默认只生成不自动打开；如需自动拉起浏览器，设置 `app.open_browser: true`

## Scoring JSON

评分已经外置成 JSON：

- 运行文件：`config/feishu_digest_scoring.json`
- preset 模板：`config/presets/finance_max/feishu_digest_scoring.json`

你改这个 JSON，下一次脚本执行时，摘要卡片的排序和入选 top10 就会跟着变。

最常改的键：

- `group_weights`：调整不同主题组的重要性，例如把 `宏观政策`、`大宗商品`、`中港股市`、`美股与波动率` 拉高
- `source_weights`：调整官方源、快讯源、社交源的基础分
- `market_priority_keywords`：命中这些词就加分，适合强化 A 股 / 港股 / 美股 / 利率 / 黄金 / 原油
- `low_signal_patterns`：命中这些英文低信号标题模式就扣分
- `low_signal_penalty_per_match`：低信号惩罚力度
- `minimum_score`：最低入选分，达不到就不进卡片，所以最终可以少于 10 条

## Scheduler

这套方案不是常驻服务，不需要额外起 daemon。

实际运行方式就是外部定时执行：

- 每次执行 `uv run python -m trendradar`
- 程序会先采集，再看当前是否命中时间窗，决定要不要分析和推送
- `finance_digest_card` 预设下：
  - `08:00-08:30` 晨报窗口，窗口内只推一次
  - `22:00-22:30` 晚报窗口，窗口内只推一次
  - 其他时间只采集，不推送

建议：

- 本地 `cron` / `launchd` / Docker cron 均可
- 采集频率建议 `15~30` 分钟
- 如果你想少漏掉盘中快讯，建议 `15` 分钟跑一次

## LLM Client

TrendRadar 的 AI 分析、AI 筛选、AI 翻译现在统一走共享 `llm_client`：

- 文件：`/Users/NikoBelic/app/git/core-common-tools/core_common_tools/llm_client.py`
- 默认链路：共享 client 内部决定 provider 顺序
- `ai.api_key` / `ai.api_base` 现在是可选覆盖项，不再是必须先配好的前置条件

摘要卡片里的英文标题翻译，以及「两次推送间重大影响」总结，也都走这条共享链路。

## AI Model

当前预设默认还是兼容性更高的 `deepseek/deepseek-chat`。

如果你要把分析质量再往上拉，优先自己改：

- `ai.model`
- `ai.max_tokens`
- `ai_analysis.max_news_for_analysis`

更强模型适合：

- 晚间深挖
- 周末策略复盘
- 多源去重后的跨资产联动总结

## What This Solves

这套 preset 已经把 TrendRadar 在公开数据层能做的财经方向基本拉满了：

- 官方披露层
- 快讯层
- 社交情绪层
- 分时段策略
- 关键词与 AI 双路径筛选
- 晚间和周末深度分析

## What It Still Cannot Replace

它依然不是这些东西的替代品：

- Wind / Bloomberg / FactSet / Refinitiv
- 实时逐笔行情和盘口
- 东方财富深度资金流、公告结构化数据库
- 券商研报全文库
- 公司自定义 IR feed 的全覆盖

如果你还要继续往“最强”推进，下一层应该补：

1. 东方财富 / 交易所 / 巨潮的结构化链路
2. 自选股和行业 watchlist 的专属 IR 源
3. 海外社媒实时源（X / Reddit / YouTube 财经频道）
4. 更细的盘中调度和事件分级推送

## OpenClaw Push

如果要把这套方案摘要推给你的飞书 agent：

```bash
bash /Users/NikoBelic/app/git/core-common-tools/scripts/openclaw_feishu_skill.sh \
  send-via-agent --agent crawler --message "TrendRadar finance_max 已部署，去看 docs/FINANCE_MAX_SETUP.md"
```
