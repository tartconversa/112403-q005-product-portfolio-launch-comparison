# 多品类新品成长基准与资源转配评审

合成新品商品谱系、渠道可售期间、净交易和内容资源样例。

All example identifiers and values are synthetic. CSV files use UTF-8. The first row contains column names, and an empty cell means the source supplied no value.

## Sample files

- `products.csv` contains 新品、前代及商品版本的谱系关系。 Columns are `sku`, `category`, `predecessor`, `version`, `launch`.
- `availability.csv` contains 商品在渠道的有效上架和可售天数。 Columns are `sku`, `channel`, `from`, `to`, `days`, `reason`.
- `performance.csv` contains 商品阶段净购买和内容资源投入。 Columns are `sku`, `window`, `units`, `refunds`, `spend`.

Python 3 standard library is sufficient to inspect these inputs. Run `python3 -m unittest discover -s tests -v` to check the delivered file structures and example references.

---

# 新品成长基准与资源转配评审（应用）

一个只用 Python 标准库的本地评审应用。它解决的不是「把首月销售额并排」，而是：

- 按**上市后同一阶段**对齐，并按**共同可售日**归一净购买；
- 谱系、上市日期、渠道可售、版本变更、退款任何一项无法确认时，商品进入**待核**，
  不按 0 销售、不按满月在售参与简单排名；
- 资源建议**永不削减已批准/已签约排期**，只从未承诺预算里给追加，并列出库存、
  已签档期、同类新品竞争、品类预算等约束；
- 评审决定固化当时依据；补录库存或修订谱系后，团队能看到排名与建议怎么变，
  已批准计划进入复核队列而不会被改写。

## 启动

```bash
python3 -m app.server --data data/sample --port 8000
# 打开 http://127.0.0.1:8000
```

可选参数：`--ledger data/decisions.jsonl` 指定决定台账路径；`--host 0.0.0.0` 允许局域网访问。
不需要安装任何第三方包。

## 页面

| 路径 | 内容 |
|---|---|
| `/` | 总览：KPI、按品类分面的净购买强度、阶段延续、建议摘要、复核队列 |
| `/products` | 商品列表（含前代）；筛选行统一控制阶段 / 客群 / 数据截止 |
| `/product/<sku>` | 阶段指标、为什么可比/不可比、与前代共同可售比较、渠道可售、时间线、决定表单 |
| `/resources` | 按建议分组的资源建议、约束条件、保留承诺 |
| `/revisions` | 两个数据截止版本之间的排名 / 建议差异 |
| `/decisions` | 复核队列与全部决定台账 |
| `/method` | 口径、规则和数据完整性自查 |
| `/api/rankings`、`/api/product?sku=`、`/api/recommendations`、`/api/revisions`、`/api/decisions` | JSON |

## 输入文件

除原有三个文件外，应用会读取（不存在则跳过）：

| 文件 | 关键列 | 用途 |
|---|---|---|
| `products.csv` | sku, name, category, predecessor, variant_of, version, launch, launch_status, launch_confirmed_by, lineage_status, lineage_reason, audience, audience_confirmed_by, unit_margin, is_new, discontinue_on | 谱系与客群；`lineage_status=pending` 即待核 |
| `availability.csv` | sku, channel, from, to, listed, days, reason, stock_status, oos_from, oos_to, oos_days, added_on, confirmed_by | 渠道可售日历；`oos_*` 为缺货区间 |
| `sales_daily.csv` | sku, date, units, refunds, spend, channel | 按日销售（优先） |
| `performance.csv` | sku, window, from, to, units, refunds, spend, channel | 窗口级汇总（回退口径） |
| `inventory.csv` | sku, channel, on_hand, in_transit, weeks_of_cover, as_of | 前代/新品库存约束 |
| `events.csv` | sku, event_type, from_version, to_version, effective_on, gap_days, changed, confirmed | 配方/包装变更 |
| `audience_groups.csv` | group_id, label, skus | 同客群对照 |
| `content_plans.csv` | plan_id, sku, stage, channel, amount, start, end, status, signed_on | 内容排期；`signed`/`approved`/`committed` 视为已承诺 |
| `lineage_revisions.csv` | revision_id, sku, changed_on, from_predecessor, to_predecessor, decided_by, audience | 谱系修订及其生效日 |
| `plan_decisions.csv` | decision_id, plan_id, sku, status, decided_on, plan_amount, actor, basis_json | 历史评审决定；首次运行导入台账后不再覆盖 |

空单元格表示来源没有提供该值；应用不会把它补成 0。

## 口径要点

- **上市阶段**：上市月 1/2/3 = 0–29 / 30–59 / 60–89 天，按上市后第 N 天对齐。
- **共同可售**：渠道上架区间 − 缺货 − 未到实际首卖日 − 待核区间。
  多渠商品以「渠道可售日之和」为强度分母，并另报自然日并集。
- **净购买** = 订单 − 退款；退款缺失或超过订单量时待核。
- **复购信号** = 月 2 净购买 ÷ 月 1 净购买（阶段延续代理，非用户级复购）；
  相邻阶段可售不足 20 天不计算。
- **每单位资源贡献** = 净购买 × 单位毛利 ÷ 内容投入；缺毛利时退回净购买/元资源并标注。
- **待核一票否决排名**：谱系待核、上市未确认、渠道记录无运营确认、库存状态未核实、
  声明天数与日历推导不符、长期缺货（窗口内缺货 ≥ 30%）、版本变更未核实、客群不匹配等。
- **「不在所选客群」与「待核」分开**：前者是筛选范围问题，换客群即可，不是数据缺失。
- **资源建议**：只有月 2 复购 ≥ 80%、月 2/月 1 ≥ 60%、每元资源贡献不低于同品类
  中位数（至少 2 个可比对照）且库存充足时才给「可小幅追加」；额度还会被前代库存、
  已签档期重叠、同类新品竞争、品类预算依次压低。已承诺排期只列不改。
- **修订回放**：渠道按 `added_on`、库存按 `as_of`、谱系按 `changed_on` 回放；
  同一渠道取最新一条。修订对比页可查看排名与建议的变化。
- **决定台账**：写入 `data/decisions.jsonl`，每条保存当时的完整证据快照；
  后续变化进入复核队列，原依据不被覆盖。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_sample_inputs.py` 验证原始样例表结构；
`tests/test_review.py` 覆盖可售裁剪、待核、复购、资源约束、决定台账与修订对比。
