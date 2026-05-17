from pathlib import Path
p = Path("/Users/bytedance/PycharmProjects/stock/docs/SOP.html")
html = p.read_text(encoding="utf-8")

toc_old = '''    <li><a href="#v11">v1.1 升级清单 &amp; 已知局限</a></li>
  </ol>'''
toc_new = '''    <li><a href="#v11">v1.1 升级清单 &amp; 已知局限</a></li>
    <li><a href="#monday-sop">周一开盘日 SOP(每周一对照执行)</a></li>
    <li><a href="#monday-plan">2026-05-18 周一交易计划(基于 05-15 收盘)</a></li>
  </ol>'''
assert toc_old in html, "toc anchor not found"
html = html.replace(toc_old, toc_new, 1)

new_sections = r'''
<!-- ============ 14. 周一开盘日 SOP ============ -->
<section class="card" id="monday-sop">
  <h2>⑭ 周一开盘日 SOP(每周一对照执行)</h2>
  <p class="callout">周末两天没有行情更新,周一开盘前的 phase 判断滞后,且周五的连板梯队需要在周一第一个小时内复核。本节是<strong>每周一 08:30—15:30 的标准动作清单</strong>。</p>

  <h3>08:30 · 开盘前准备</h3>
  <ol>
    <li>确认 v1.1 卖出策略已开:
<pre><code>cd ~/PycharmProjects/stock
.venv/bin/python -c "from stockbot.core.config import load_config; c=load_config('config.yaml'); de=c['dragon_head_exit']; \
print('enabled:', de['enabled'], 'emergency_phases:', de['emergency_exit_phases'], \
'break_board:', de['break_board_exit'], 'momentum:', de['momentum_failure_exit'], 'theme_dead:', de['theme_dead_exit'])"</code></pre>
    </li>
    <li>检查 paper_account 起手状态:<code>cat stockbot/data/paper_account.json</code></li>
    <li>备份周五 dragon_pool / market_context(回溯需要):
<pre><code>mkdir -p stockbot/data/snapshots/$(date +%Y%m%d)
cp stockbot/data/dragon_pool.csv stockbot/data/snapshots/$(date +%Y%m%d)/
cp stockbot/data/market_context.json stockbot/data/snapshots/$(date +%Y%m%d)/</code></pre>
    </li>
  </ol>

  <h3>09:00 · 集合竞价前 morning_call</h3>
  <pre><code>.venv/bin/python scripts/morning_call.py --config config.yaml 2&gt;&amp;1 | tee logs/morning_call_$(date +%Y%m%d).log</code></pre>
  <p>预期:昨日 watch_list_tomorrow.csv 里的封板龙头会过 <code>call_auction_filter</code>。高开 &gt; 7% 跳过、低开 &lt; -2% 跳过,通过的票写入 watch_list_today.csv。</p>

  <h3>09:30 · 主流程(daily_run)</h3>
  <pre><code>.venv/bin/python scripts/daily_run.py --config config.yaml --top 20 --limit 50 2&gt;&amp;1 | tee logs/daily_run_$(date +%Y%m%d).log</code></pre>
  <p>四步顺序执行:</p>
  <ol>
    <li><code>build_universe</code> — 用 AkShare 拉今日 spot,score 前 50 写入 universe.csv</li>
    <li><code>build_market_context</code> — 当日 phase 判定(ferment / repair / cooldown / panic)</li>
    <li><code>build_dragon_pool</code> — 题材-梯队-板块地位打分,产出 dragon_pool.csv(20-50 条)</li>
    <li><code>run_trader</code> — TradingEngine.execute() 优先级:<strong>v1.1 龙头式卖出 &gt; 通用止损 &gt; 新买入</strong>。空仓时只有买入触发</li>
  </ol>
  <p class="callout warn"><strong>建议把 daily_run 推迟到 09:35—09:40 跑</strong>:09:30 整点 phase 统计的是开盘前快照,phase 推断不准。</p>

  <h3>10:00 / 11:00 / 14:00 · 盘中风控复跑(可选)</h3>
  <p class="callout warn"><strong>当前 v1.1 仍是日终一次性触发</strong>,<code>break_board_exit</code> / <code>momentum_failure_exit</code> 设计为盘中实时,但目前在 <code>daily_run.py</code> 一天跑一次的模式下会滞后到次日。修法见 v1.2 计划:加 <code>scripts/eod_sell_check.py</code> 在 14:55 跑一次 sell-only 分支。</p>

  <h3>14:55 · 收盘前 EOD check</h3>
  <pre><code># 复用早盘 universe,只重建 dragon_pool + 跑 v1.1 卖出 + 不下买单
.venv/bin/python scripts/daily_run.py --config config.yaml --skip-universe 2&gt;&amp;1 | tee -a logs/eod_$(date +%Y%m%d).log</code></pre>
  <p>这一步让 v1.1 卖出在尾盘抓到当天炸板/题材哑火,而不是次日。</p>

  <h3>15:30 · 收盘后日终核对</h3>
  <pre><code>grep -E "BUY|SELL|reject|warn" logs/daily_run_$(date +%Y%m%d).log
cat stockbot/data/paper_account.json | python3 -m json.tool
grep -E "dragon_exit|emergency|break_board|theme_dead|momentum_failure" logs/*$(date +%Y%m%d)*.log
cat stockbot/data/watch_list_tomorrow.csv</code></pre>

  <h3>周一可能遇到的特殊场景</h3>
  <table>
    <thead><tr><th>场景</th><th>判定信号</th><th>动作</th></tr></thead>
    <tbody>
      <tr><td>周五龙头周一一字低开</td><td>蒙娜丽莎/利仁科技竞价 &lt; -3%</td><td>市场转 cooldown,<strong>当日不开仓</strong>;持仓全部 emergency_exit</td></tr>
      <tr><td>题材接力失败</td><td>dragon_pool 板块当日无新涨停 + 龙头连板断</td><td><code>theme_dead_exit</code> 触发,持仓 T+1 开盘卖</td></tr>
      <tr><td>个股开板炸板</td><td>open_times ≥ 1 且 30 分钟未回封</td><td><code>break_board_exit</code> 触发,收盘前一刻卖</td></tr>
      <tr><td>放量滞涨</td><td>成交额 ≥ 昨日 1.5x 但涨幅 &lt; 3%</td><td><code>momentum_failure_exit</code> 触发,半仓减出</td></tr>
    </tbody>
  </table>
</section>

<!-- ============ 15. 周一交易计划 ============ -->
<section class="card" id="monday-plan">
  <h2>⑮ 2026-05-18 周一交易计划(基于 2026-05-15 周五收盘)</h2>

  <h3>盘面定调</h3>
  <ul>
    <li><strong>周五涨停队伍</strong>:dragon_pool 共 45 只封板/接近封板,但 lu_count ≥ 2 的连板梯队仅 4 只</li>
    <li><strong>梯队龙头</strong>:蒙娜丽莎(6 板)、利仁科技(5 板)、威龙股份(3 板)、和远气体 / 北自科技(2 板)</li>
    <li><strong>市场情绪</strong>:market_context.json 当前 phase=<code>unknown</code>,周一 09:30 需先跑 build_market_context 重判。从涨停宽度看实际偏 <strong>ferment(发酵)</strong></li>
  </ul>

  <h3>题材集中度(出现 ≥ 2 次的板块)</h3>
  <table>
    <thead><tr><th>板块</th><th>次数</th><th>代表票</th></tr></thead>
    <tbody>
      <tr><td>汽车零部</td><td>4</td><td>通达电气、新坐标、中马传动、东风科技</td></tr>
      <tr><td>化学制品</td><td>4</td><td>中欣氟材、和远气体、金石资源、巍华新材</td></tr>
      <tr><td>自动化设备</td><td>3</td><td>雷赛智能、三丰智能、北自科技</td></tr>
      <tr><td>家居用品</td><td>3</td><td>蒙娜丽莎(6 板龙头)、共创草坪、丰林集团</td></tr>
      <tr><td>电机Ⅱ</td><td>3</td><td>方正电机、华新精科、科力尔</td></tr>
    </tbody>
  </table>

  <h3>第一档 · 核心标的(连板龙头 / 情绪指标 · 不直接参与)</h3>
  <table>
    <thead><tr><th>票</th><th>板位</th><th>板块</th><th>周五现象</th><th>周一动作</th></tr></thead>
    <tbody>
      <tr><td>蒙娜丽莎 002918</td><td><strong>6 板</strong></td><td>家居用品</td><td>开板 3 次最终封死,封单 6.3 千万</td><td><strong>不参与</strong>,只看竞价 → 情绪指标</td></tr>
      <tr><td>利仁科技 001259</td><td>5 板</td><td>小家电</td><td>一字板,封单仅 3.4 千万</td><td>跳过,一字封死无量</td></tr>
      <tr><td>威龙股份 603779</td><td>3 板</td><td>非白酒</td><td>一字板,封单 5.2 千万</td><td>跳过,一字</td></tr>
    </tbody>
  </table>
  <p class="callout warn">6 板龙头 <strong>不是周一加仓对象,是情绪指标</strong>。它若周一直接炸板 → 全市场 cooldown,触发全清警报;它若再封 → 板块继续,二三梯队接力空间打开。</p>

  <h3>第二档 · 接力候选(09:30 集合竞价跟踪)</h3>
  <table>
    <thead><tr><th>票</th><th>板块</th><th>周五涨幅</th><th>封单</th><th>开板次数</th><th>集合竞价决策</th></tr></thead>
    <tbody>
      <tr><td>📌 中巨芯-U 688549</td><td>电子化学(科创板)</td><td>20.0%</td><td>1.65 亿</td><td>1</td><td><strong>重点</strong>:科创板首板 20%,封单大,板块独苗。高开 ≤ 7% 可上,&gt; 7% 跳过</td></tr>
      <tr><td>📌 三丰智能 300276</td><td>自动化设备(创业板)</td><td>19.9%</td><td>1.05 亿</td><td>0</td><td>创业板 20%,封单中等,<strong>只追低开 / 不高开追</strong></td></tr>
      <tr><td>三瑞智能 301696</td><td>航空装备(创业板)</td><td>20.0%</td><td>7165 万</td><td>2</td><td>⚠️ 开板 2 次,跳过</td></tr>
      <tr><td>多氟多 002407</td><td>化学制品</td><td>10.0%</td><td>2.08 亿</td><td>3</td><td>❌ 开板 3 次,剔除(超过 max_open_times)</td></tr>
      <tr><td>✅ 中欣氟材 002915</td><td>化学制品</td><td>10.0%</td><td>1.68 亿</td><td>0</td><td><strong>首选化学制品接力</strong>:板块同步、首板未开</td></tr>
      <tr><td>✅ 雷赛智能 002979</td><td>自动化设备</td><td>10.0%</td><td>1.59 亿</td><td>0</td><td>自动化设备分支龙头候选</td></tr>
      <tr><td>方正电机 002196</td><td>电机Ⅱ</td><td>10.0%</td><td>1.35 亿</td><td>1</td><td>⚠️ 开板 1 次,边缘可观察</td></tr>
      <tr><td>北自科技 603082</td><td>自动化设备</td><td>10.0%</td><td>7147 万</td><td>1</td><td>⚠️ 2 板但开板,封单中等</td></tr>
    </tbody>
  </table>

  <h3>第三档 · 规避清单</h3>
  <ul>
    <li>多氟多 002407(开板 3 次)</li>
    <li>和远气体 002971、好利科技 002729、瑞泰科技 002066、中视传媒 600088 等开板 ≥ 1 次 + 封单 &lt; 8000 万的票</li>
    <li>共创草坪 605099(53.56 元 + 换手仅 1.5%):缩量封板、跟风</li>
  </ul>

  <h3>实战执行</h3>
  <ol>
    <li><strong>09:15-09:25 集合竞价</strong>:
      <ul>
        <li>看蒙娜丽莎、利仁科技竞价 — 若两个 5+ 板都低开 -3% 以上 → 市场转 cooldown,<strong>当日不开仓</strong></li>
        <li>看第二档候选竞价价格,过 call_auction_filter(高开 ≤ 7% 且 ≥ -2%)</li>
      </ul>
    </li>
    <li><strong>09:30-10:00 主仓位</strong>(假设盘面正常):
      <ul>
        <li>首选 1:中巨芯-U 688549 — 科创板 20% 首板独苗</li>
        <li>首选 2:中欣氟材 002915 — 化学制品板块二线接力,首板未开</li>
        <li>备选:雷赛智能 002979(自动化设备分支)</li>
        <li>仓位:首日只开 2 只 × 25% 仓 = 总仓 50%(留弹药给周二盘后)</li>
      </ul>
    </li>
    <li><strong>盘中风控(v1.1 自动)</strong>:
      <ul>
        <li>持仓票当日开板 ≥ 1 次 → break_board_exit(30 分钟不回封即卖)</li>
        <li>板块当日无新涨停 + 龙头断板 → theme_dead_exit(T+1 开盘卖)</li>
        <li>放量 1.5x 但涨幅 &lt; 3% → momentum_failure_exit(半仓减出)</li>
      </ul>
    </li>
    <li><strong>14:55 收盘前</strong>:跑 <code>daily_run.py --skip-universe</code>,让 v1.1 在尾盘抓破位</li>
  </ol>

  <h3>真实判断(必读)</h3>
  <p class="callout warn">周五涨停队伍 <strong>梯队结构很差</strong> — 6 板 + 5 板各 1 只(都是一字),3 板 1 只,2 板只有 2 只,中间断层。这种结构在龙头战法里叫"<strong>伪发酵 / 一日游接力风险大</strong>",不是优质开仓窗口。</p>
  <p class="callout warn">题材分散 — 化学制品 4 只里 3 只是冷门小盘,汽车零部 4 只都是各自独立题材,<strong>没有形成主流热点</strong>。</p>
  <p class="callout"><strong>纪律建议</strong>:严格执行龙头战法,周一最稳的动作是 <strong>空仓观察</strong>,等周一收盘看蒙娜丽莎能否封死 7 板 + 二板梯队是否扩到 5 只以上,周二再考虑开仓。<br>
  想开仓的话,只买 <strong>中巨芯-U + 中欣氟材</strong> 两只,各 20—25% 仓位,严格设 -3% 止损 / 炸板秒卖。</p>
</section>

'''

footer_anchor = "<footer>"
assert footer_anchor in html
html = html.replace(footer_anchor, new_sections + footer_anchor, 1)

p.write_text(html, encoding="utf-8")
print("ok, total lines:", html.count("\n") + 1, "size:", len(html))
