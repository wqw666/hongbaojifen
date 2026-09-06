# 复合玩法重做设计（大吃小×抢庄，唯一玩法）

日期：2026-09-06 ｜ 状态：待用户评审
范围：agent 玩法引擎 + GUI 玩法页 + 总后台玩法收敛。不涉及 DB 迁移。

## 0. 背景与决策记录

用户要求：删除全部 6 个旧玩法，只保留一个「复合玩法」（大吃小兼容抢庄），agent 游戏玩法页
改为该唯一玩法的操作台。经澄清（AskUserQuestion，2026-09-06）确认的决策：

| # | 问题 | 决策 |
|---|------|------|
| D1 | 同点数组内盈亏分配 | **按注序逐个处理**（规则6字面）：组占一个名次；组赢时先下注者先拿满（封顶=自己注额），再轮到下一个；组输/被抽水时先下注者先被扣到见底，再下一个 |
| D2 | 编辑表格改开奖的粒度 | **改「抢到金额」**（0.xx 元）→ 自动重算点数/名次/盈亏；下注列只读 |
| D3 | 有人一直不抢红包 | **红包领完即可结算**，未领者点数按 0 输光；管理员可在表格补开奖金额后再结算 |
| D4 | 旧玩法/总后台范围 | **全量收敛**：总后台删 6 条旧 play_rule_files 记录及磁盘文件，只留「复合玩法」；agent/play_rules 只留新规则文件 |
| D5 | 多游戏群 | **可多群同时各开各的局**：玩法状态按群隔离；GUI 群切换器决定按钮与表格作用对象 |
| D6 | 红包不足判定 | **只认管理员发的第 1 个红包**：其 total_num < 本局需开奖人数 → 领完即作废，不可补发第 2 个 |
| A1 | 需开奖人数口径 | 大吃小 = 下注人数；抢庄 = 下注人数 + 庄家 1 份（庄家需红包定自己的点数）——**用户原话只写「下注人数」，此为采纳推荐口径，评审可改** |
| A2 | 已下注者不能发「撑」 | 保护下注名单完整性——**采纳推荐口径，评审可改** |
| A3 | 作废判定时点 | 封盘时刻若有首红包且已知不足 → 立即作废；否则每次该红包事件到达复查，领完（recv≥total）仍不足 → 作废 |

约束（沿用仓库记忆）：不主动 git commit；agent 更新需 bump `gui/app/main_window.py` APP_VERSION
并同步 `agent/docs/代码架构说明.md`；总后台 data/rules 存储与 agent/play_rules 双仓库同步；
新增 Flyway 迁移须唯一递增版本（本次不需要迁移）。

## 1. 玩法文件 `rule_fuhe.py`（agent/play_rules/ 唯一规则文件）

模块级状态按群键控（`int(group_id)` → 局状态），彻底解决旧 rule_dcxx/rule_laoda 的
BETS/PHASE 跨群共享串群问题：

```python
ROUNDS: dict[int, RoundState]   # RoundState: {
#   phase: idle|betting|sealed   (settled 后即清出 ROUNDS，不设该态)
#   round_id: str, bets: list[{qq,nickname,amount,ts}]   # 保序 = 下注顺序（规则6 依据）
#   boss: {qq,nickname}|None, mode: "dcxx"|"qzz"         # qzz = 有人撑
#   sealed_msg: 封盘汇总公告文本（封盘时生成）
# }
```

### 1.1 消息与命令（handle_message 返回 str|None，delta 恒 0 —— 积分只经红包结算产生）

| 输入 | 条件 | 行为 |
|---|---|---|
| 「开始游戏」「开局」 | 群内 | 同 handle_round_start：重置该群进入 betting，返回玩法开场白 |
| 纯数字 / `下注N` | betting 且已有人撑 | 非庄家 → 登记挑战注，回复「本局{局号}，庄家{名}，当前押注：累计名单」；庄家本人 → 「坐庄不用下注」 |
| 纯数字 / `下注N` | betting 且无人撑 | 登记为普通注（大吃小口径），回复「本局{局号}，累计名单」 |
| 「撑」 | betting 且无庄 | 发撑者成为庄家（先到先得）。**若发撑者已下注 → 拒绝并提示「已下注不能抢庄，等待下一局」（A2）**；成功 → 公告「🎤 X 抢到庄家！…本局转抢庄模式，其余人发数字押注挑战」 |
| 「撑」 | betting 已有庄 | 回复「{庄家} 已是本局庄家，不能重复抢庄」 |
| 「撑」 | 非 betting | 「当前未开局/已封盘」提示 |
| 纯数字 | 非 betting | None（闲聊静默，现有行为） |
| 其它 | 任意 | None |

### 1.2 比大小（排序算法，两种模式共用）

金额经 `f"{amount:.2f}"`（amount∈[0.01,0.99]，必为两位小数），取两位 cents 数字 (a,b)：

1. **点数 pts = (a+b) % 10**（0.77 → 7+7=14 → 4；0-9）
2. 排序键依次：pts 降序 → cents 中 0 的个数降序 → max(a,b) 降序 → **下注/登记顺序升序**
   - 0.90 vs 0.81：pts 9=9 → 0 数 1>0 → 0.90 大
   - 0.27 vs 0.36：pts 9=9、0 数 0=0 → 7>6 → 0.27 大
3. 前三键全同 → 视作**同一名次组**（规则6）：组内按下注顺序（bets 列表序）逐个处理盈亏。
   排序键第 4 档仅作组内序，不影响名次划分。

> 注意与旧代码差异：旧 `_digits` 返回三位原始和（0.91→10点），新规则取 **个位**（0.91→1点）——
> 排序结果会与旧大吃小不同，属于用户规则 2 的字面要求（0.77→点4、范围 0-9）。

### 1.3 大吃小模式结算（无庄）

输入：封盘后的最终 claims（每人金额，含管理员表格补值/改值，见 §3.3）+ rate_permille。
返回 `{events:[{qq,nickname,reply,delta}], announce}`（`delta>0` 赢 / `<0` 亏 / `0` 平）。

算法（与用户示例逐分验算一致）：
1. pool = Σ下注额；fee = pool × clamp(rate,0,1000) ‰ **向下取整**（2% 例：400→8）
2. 参与人 = 所有下注者。未出现在 claims 的下注者金额按 0 计（点数 0，排最后）
3. **名次**：按 §1.2 排序；同键合并为名次组（组序固定）
4. **吃**：除最末名次组外，从最高名次组开始，每组需吃额 = Σ该组下注；从**最低名次组**的本金
   向上逐个名次吃，吃到自己名次为止（不碰同级及以上本金），吃够即停
5. **组内分配（D1）**：组的赢额/被吃额/被抽水额，按组内**下注顺序逐个**消化——先下注者先拿满
   （赢时封顶=自己注额）或先被扣完（输时到底=自己注额），剩余给后下注者；组额不足以覆盖全组时
   先下注者优先（后者得零头或 0）
6. **抽水**：总抽水从**吃剩的最低位名次组**开始向上收（同上组内按注序消化）；吃净仍不足则
   顺延次低组，直到收齐；仍不足（极端：全是同点一组）则从最高位组依次补收
7. delta = 吃赢额 − 被吃额 − 被抽水（即收付结算）；每人都拿回自己本金再按 delta 加减

**验收向量（用户示例，直接编入 selftest）**：A/B/C/D 各注 100/20/80/200，点数 A>B>C>D，
rate=20‰：pool 400、fee 8；A +100、B +20、C +72（拿回 152）、D −200。Σ玩家 delta = −8 = −fee ✓

同点组序验收向量：X、Y 同点并列最低组各注 200/100（X 先下注），上一名次组吃走 250 + 抽水 30
共 280：X 先被扣满 200，Y 再扣 80，组内剩 20 归 Y（Y delta −80）。

### 1.4 抢庄模式结算（有人撑）

输入同上。流程：
1. 庄家余额预检（封盘时，§2 按钮 2）：`need = pool + fee`（pool=Σ挑战注，fee 同上公式；
   例 500 注 → fee 10 → need 510）。**余额不足 → 本局作废**（D 群内播报作废，不上报）
2. 参与人 = 庄家 + 挑战者。庄家在 claims 中无记录 → 庄家点数按 0 作基准（沿用 rule_laoda 语义，
   且表内可补值）；挑战者未抢 → 点数 0 **必输光**（不参与打平退回）
3. 逐挑战者与庄家点数比较：> 庄家 → 赢自己注额（delta=+注，庄家赔）；< 庄家 → 输（delta=−注，
   给庄家）；= 庄家 → 打平退回（delta=0）；**同点组内若多个挑战者同点 → 各自独立对庄家结算
   （庄家是唯一对手，无组内吃池），无顺序问题**
4. 庄家 delta = Σ(输者注) − Σ(赢者注) − fee（抽水庄家承担）；庄家事件 reply 汇总三项
5. 播报 announce 逐行挑战者结果 + 庄家净得

**验收向量（用户示例）**：甲撑（庄），乙丙丁戊注 20/80/300/100，点数 乙>丙>甲=丁>戊，rate=20‰：
pool 500 fee 10 need 510；乙 +20、丙 +80、丁 0（打平）、戊 −100；庄家 delta = −20−80+0+100−10
= **−10** ✓

### 1.5 局生命周期回调（现有协议 + 新增可选协议）

沿用：handle_round_start/end/abort、bettor_qqs（=挑战者 ∪ 普通下注者，**不含庄家**，领完判定用）、
betting_open、handle_redpacket（领取即记点数并 @ 回复，delta 0；未下注者提示不计分；金额>0.99
→ 回复「异常金额(>0.99)，请管理员在开奖表修正或作废本局」且**不记点数**，A3 相关）、
settle_redpacket（§1.3/§1.4 分派，mode 决定）。

新增可选协议（play_engine 增加一个通用转发 `call_rule(fn_name, group_id, *args)`，热重载语义同
现有包装器；新玩法实现即用，旧玩法已删不冲突）：

| 函数 | 时机/返回 |
|---|---|
| `handle_seal(group_id)` | GUI 点「停止下注」。下注者为 0 → 直接作废（返回 `{ok:False, text:作废公告}`）；否则 phase→sealed，返回 `{ok:True, text:封盘汇总公告(见 §2), banker_check:{qq, need}|None}`（banker_check 仅抢庄模式，need=pool+fee） |
| `handle_void(group_id)` | GUI 或编排层判作废 → 返回作废公告文本并复位该群状态（不抽水不上报） |
| `claim_need(group_id)` | 返回本局需开奖人数：有庄=下注人数+1，无庄=下注人数；非 betting/sealed 返回 0（供红包不足判定 A1/A3） |
| `bet_snapshot(group_id)` | 按注序返回下注明细 `[{qq,nickname,amount}]`；若有人撑，末尾追加庄家行 `{qq,nickname,amount:0,boss:true}`（供 GUI 表格：庄家行下注列显示「坐庄」） |
| `seal_info(group_id)` | 当前状态快照（phase/mode/round_id/boss/池/费）供 GUI 状态栏 |

### 1.6 状态复位规则

- handle_round_end / handle_void：清该群 ROUNDS 条目 → idle
- 文件热重载（改玩法上传）会清所有局状态：与旧行为一致，文档注明「更新玩法文件请先无进行中局」
- 多群：ROUNDS 以 group_id 为键；每群独立 phase——「开始本局」只影响该群（改掉旧的
  BETS.clear() 全局清空语义）

## 2. GUI 游戏玩法页（agent/gui/app/play_tab.py 重构）

页内新增「当前群」下拉（默认上次操作群；群勾选列表仍来自群管理页），以下按钮/表格均作用于
当前群；其它群的局后台照常推进。

顶栏按钮（替换旧布局）：
1. **开始本局**：生成局号 hongbaojifen_{seq} → rp_game.start_round + engine.notify_round_start
   + @全体 开场白「开始游戏！本局对局编号：…」+ 表格清空 → 按钮 2/4 亮
2. **停止下注**：engine.call_rule("handle_seal", gid) →
   - 返回作废 → @全体 作废公告、关局（rp.end_round 不上报路径）、表格清空
   - 返回 banker_check → `_query_points(banker_qq)`（30s 缓存）不足 → 同上作废（播报含
     「庄家余额不足(有 X 需 Y)」）；足 → @全体 播报封盘汇总文本（下注人数、逐人昵称+注额、合计、
     抽水率、红包要求：份数≥需开奖人数、每份≤0.99）→ 按钮 3 亮，玩家消息进入「非 betting」
     拦截（封盘后纯数字/下注词回复「本局已封盘，等待开奖」）
3. **结算**（亮起条件：phase=sealed 且本局存在；若红包未领完/未见到首红包点击 → 弹确认框）：
   - 合并 claims：ann.claims（已按 §3.3 可改/可补）+ 庄家/未领下注者如被表格补值则补入
   - engine.handle_redpacket_batch（settle_redpacket）→ 逐人 @ 回复 + @全体 announce（现有
     `_announce_round_summary` 复用 announce_text 分支）
   - 上报 `/api/open/games/report`（局号幂等；上报成功才播报——沿用「说出口=已入账」纪律）→
     关局（notify_round_end 清规则态）→ 按钮复位；上报失败 → pending 待重试（沿用
     pending_announced 机制与「重试未上报」按钮）
4. **作废本局**（phase=betting/sealed 亮）：rpg end_round 提前终止路径（handle_round_abort →
     播报终止/或已封盘则播「已作废，等待重新开局」）→ 关局清态

删除：玩法列表/启用玩法/停止玩法/连续开局；保留：费率保存（heartbeat 上报 game_fee_rate，
结算 rate 沿用 payload rate_permille）、最小下注保存、群列表刷新。新增「玩法文件更新」按钮：
download_rule(复合玩法 id) → engine 按 mtime 热重载（替代原「启用时下载」唯一触发点）。
GUI 启动/登录后自动：拉取总后台 active 玩法列表 → 找到复合玩法 → 若未激活或文件缺失则下载并
activate（替代 _auto_restore 的启用逻辑；无总后台时用 %APPDATA% 缓存兜底）。

### 2.1 右侧开奖表格（新）

- 位置：玩法页下半/右侧半宽（现 txt_log 日志区旁，左右分栏，样式抄实时监控页
  grid_columnconfigure paned 布局）；`ttk.Treeview` 列：昵称｜下注(只读)｜抢到金额(可改)｜点数｜大小(排名/相对庄家)
- 数据：群切到某群 → 若该群 ROUNDS 有局（betting/sealed）：行 = bet_snapshot 保序；
  claims 金额/点数合并显示；未抢者金额列空显示「未抢」；金额 >0.99 标红提示
- 编辑：Treeview 无原生编辑 → 双击「抢到金额」格弹出输入框（或选中行 + 底部输入框+确认）：
  - 值须 0.01–0.99 两位小数；保存 → 写 ann["claims"][qq]（新值 + edited 标记；原值留存 logs）→
    重算该行点数与排名即时刷新表格（排行重算在结算时才定稿，表格仅预览）→ 日志记录「管理员
    改开奖：X 0.xx→0.yy」
  - 未抢的下注者/庄家同样可补值（D3），补值视同抢到该金额
- 下注列只读（D2「不能修改下注」）；加注/撤注只存在下注期由群消息自然进行，不提供表格直改
- 结算后：表格保留「本局已结算」结果行直至下次开始本局/切群（便于回看）

### 2.2 消息文案模板（统一进 play_tab/rule，与现有 announce 风格一致）

- 开场白（rule handle_round_start 返回）：`游戏开始！本局对局编号：{round_id}。玩法：复合玩法
  （大吃小×抢庄）：直接发数字下注（N 积分）；想当庄家的发「撑」抢占（先到先得，本局转抢庄）。
  下注结束后管理员发红包（每份≤0.99 元，份数≥需开奖人数），红包金额定大小。`
- 封盘汇总（rule handle_seal）：`停止下注！本局对局编号：{round_id}，共 N 人下注，合计 X 积分：
  \n1. 昵称 下注 A\n…\n管理员请发红包开奖（{抢庄:庄家 {名} 需另留 1 份}，每份 0.01–0.99 元，
  份数不少于 {需开奖人数} 份）。`
- 结算 announce（rule）：`本局开奖（大吃小）！总下注 {pool}，抽水 {fee}：\n逐行…`
  / `本局开奖（抢庄）！庄家 {名} 点数 {pts}，挑战押注总额 {pool}，抽水 {fee}：…庄家净得 …`
- 作废：红包不足 `红包份数 {total}<需开奖人数 {need}，本局作废（积分未扣），请重新开局。`
  庄家余额不足 `庄家 {名} 余额不足（需 {need}，实际 {balance}），本局作废（积分未扣）。`
  手动 `本局 {round_id} 已终止/作废（积分未扣），请等待管理员重新开局。`

## 3. 编排层改动（agent/integration/）

### 3.1 play_engine.py
- 新增通用 `call_rule(fn_name, group_id, *args)`：热重载守卫同 `_current_module`，调用后返回
  原始值（异常记 last_error 返回 None）——供 handle_seal/handle_void/claim_need/bet_snapshot/
  seal_info 等新协议；模块级文档补协议表
- 其余（activate/handle_message/redpacket/notify/bettors/abort/betting_open）不动

### 3.2 redpacket_game.py（本局模式改为手动结算驱动）
- handle_claim（announced 分支）改：每包事件记录后不再触发 `_schedule_round_end`；改为：
  1) 首红包判定：ann 记录 first_bill（首个管理员红包单号）与 total_num——后续不同单号事件
     **忽略**（D6 只认第 1 个；日志提示「只认第 1 个红包，多余红包不计」），同单号重复推送仍幂等
  2) 每次事件到达（含 25s 轮询补推）若 ann 已 sealed 且 first_bill.total_num <
     claim_need(gid)（engine.call_rule；缺省=len(bettors)）→ 播报作废并关局（数据不上报）——
     A3「领完即判」：total_num 已知即判，不必等 recv=total
  3) ann 记 ready_to_settle：封盘后该红包 recv_num>=total_num（领完）→ 状态刷新 GUI 提示
     「红包已领完，可点结算」；取消 10s/20s 分阶段计时与 poll_auto_end 自动收尾（该路径删除；
     保留 pending 重试、closed_bills 防幽灵、end_round 作废语义）
- 新增 `settle_round_now(group_id)`（供「结算」按钮）：校验 ann 存在且未 settled → 合并 claims
  与表格补值 → `_apply_batch_settle`（现 payload 参数改为 ann 内 claims+补值构建的 claims 列表）
  → settle 上报 → 成功才 announce + 清 announced + notify_round_end（沿用“上报成功才播报”）
- ann 结构追加字段：`sealed: bool`、`first_bill: {bill,total_num,recv_num}|None`、
  `claims[qq].edited: bool`、`ready_to_settle: bool`
- 删 STAGE_SETTLE_SEC/STAGE_DETAIL_SEC 收尾相关（_schedule_round_end/_batch_settle_once 并入
  新方法）；自测同步改（人工结算路径 + 首红包不足作废 + 幂等 + 手动作废）

### 3.3 下注/封盘期游戏消息与 RoundSession
- 纯红包玩法收敛后，RoundSession（v1 计分流）不再被复合玩法使用；保留类与 selftest 不动
  （防回归），play_tab 不再为复合玩法建 RoundSession 记账（下注消息 delta 恒 0，只记日志）

## 4. 总后台收敛（删除 6 旧玩法 → 只留复合玩法）

1. 上传 `rule_fuhe.py` 为唯一玩法（PlayRuleManager 页上传，name=「复合玩法」）→ 得 rule id
2. 删除其余 play_rule_files 行及磁盘文件：先用 admin DELETE API（或直接 SQL+手动删文件，
   先 `SELECT file_name FROM play_rule_files WHERE id=?` 逐个确认真实文件名）——6 个旧玩法
   （add1/add2/redpacket/dcxx/laoda 等）+ classpath 种子（seed_rule_add1/add2/redpacket）
3. `PlayRuleService.ensureSeedRules` 改为只兜底「复合玩法」种子（复合玩法由上传或种子保证存在）；
   同步 classpath seed 资源与磁盘残留清理
4. agent/play_rules/ 只留 rule_fuhe.py（其余删除）；`agent/docs/代码架构说明.md` §8 列表同步
5. 版本纪律：main_window.py APP_VERSION 递增 + 代码架构说明.md 行 4 引用同步（记忆规则）

> 删除 play_rule_files 行不影响历史 game_records（只存 play_name/play_id 文本/数字，无 FK 依赖
> 需在实施时确认）；总后台前端 PlayRuleManager 页面不改代码，列表自然只剩一行。

## 5. 测试策略

1. **rule_fuhe.py 内建 `_selftest`**（python 直接跑，风格同旧规则）：验收向量全覆盖——
   §1.3 用户 4 人示例、同点组注序消化示例；§1.4 用户 5 人抢庄示例（need=510、庄 −10）；
   撑先到先得/已下注拒绝/庄家免注；封盘（seal）作废路径（0 下注）；claim_need 两种模式；
   比大小排序六键逐例（0.90>0.81、0.27>0.36、0.01=0.10 注序）……；>0.99 不记点
2. **redpacket_game.py 自测更新**：手动结算路径、首红包不足作废、只认首红包（第 2 单号忽略）、
   幂等、编辑补值参与结算
3. 总后台：`mvn -Dskip.fe=true test`；agent 侧无 pytest 基建则维持内建自测惯例
4. 手工全链路（tools/e2e_full.py 或手测）：开始→下注→撑→封盘→红包不足作废；正常封盘→
   发红包→领→表格改值→结算→总后台积分核对；多群同时两局互不干扰

## 6. 实施顺序（一条条实现，见 writing-plans 产物）

1. rule_fuhe.py（含 selftest）→ 本机跑通全部验收向量
2. play_engine.call_rule 通用转发 + 协议文档
3. redpacket_game 手动结算改造 + 自测
4. play_tab GUI 重构（按钮/群切换/表格/编辑/自动激活唯一玩法）
5. 总后台收敛（种子/删除/文件同步）+ frontend 无改动验证
6. 文档与版本（APP_VERSION/代码架构说明）+ 双仓库文件同步 + 手工全链路

## 7. 未决/开放项

- 红包领取发生在封盘前（下注期管理员手滑先发包）：claims 照记、不足判定推迟到封盘（A3）；
  是否应群内提示「请等停止下注后再发红包」——评审确认
- 结算时若存在 >0.99 未修正金额：结算按钮弹窗强提示但允许继续（点数按个位规则算）还是
  强制先修正——默认**允许继续**（D2 语义下管理员自担）；评审确认
- 平衡查询用 30s 缓存，庄家刚上分未刷新可能误作废——默认接受（提示文本给实际值）；评审确认
