import React, { useEffect, useState, useCallback } from 'react'
import { Card, Row, Col, Statistic, Table, Tag, Button, Space, Tooltip, Empty, message, Descriptions } from 'antd'
import { ReloadOutlined, BarChartOutlined, DownloadOutlined } from '@ant-design/icons'
import api from '../api'

/** 整数千分位展示 */
const fmt = v => Number(v || 0).toLocaleString('zh-CN')

const gray = { color: 'rgba(0,0,0,0.45)', fontSize: 12 }

/** 卡片下的小注脚 */
const Note = ({ children }) => <div style={gray}>{children}</div>

const EXE_STATUS = {
  online: { color: 'green', text: '在线' },
  offline: { color: 'default', text: '离线' },
  banned: { color: 'red', text: '封禁' },
  orphan: { color: 'purple', text: '未绑定群' },
}

/** HTML 转义：昵称/群名/玩法名等用户文本进 HTML 前必须转义（防注入与乱排版） */
const esc = v => String(v == null ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

/** 执行器状态小徽章（下载日报用） */
const EXE_BADGE = {
  online: '<span class="st ok">在线</span>',
  offline: '<span class="st no">离线</span>',
  banned: '<span class="st bad">封禁</span>',
  orphan: '<span class="st or">未绑定群</span>',
}

/**
 * 把一次 overview 快照渲染为可留档/打印的「每日最终统计日报」HTML。
 * 与页面同一份数据、同口径；生成时间与日期写入文件内。
 */
function buildDailyHtml(data) {
  const totals = data?.totals || {}
  const today = data?.today || {}
  const month = data?.month || {}
  const dep = data?.deployment || {}
  const rk = data?.rankings || {}
  const trend = data?.trend || []
  const executors = data?.executors || []
  const ret = data?.retention_days ?? 30
  const genAt = data?.generated_at || ''
  const day = genAt.slice(0, 10) || '今日'

  const pair = (k, v) => `<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`

  // 单榜小表（今日资金/玩法榜取 amount，总积分榜取 points）
  const rankTable = (title, color, list, pointMode) => `
    <div class="rc">
      <h3>${esc(title)} Top10</h3>
      <table>
        <thead><tr><th>名次</th><th>昵称(QQ)</th><th class="r">${pointMode ? '积分(分)' : '金额(分)'}</th>${pointMode ? '<th>状态</th>' : '<th class="r">笔数</th>'}</tr></thead>
        <tbody>
          ${(list || []).length === 0
            ? '<tr><td colspan="4" class="muted">暂无数据</td></tr>'
            : list.map((r, i) => `<tr>
                <td>${i + 1}</td>
                <td>${esc(r.nickname || '—')} (${esc(r.qq)})</td>
                <td class="r" style="color:${color}">${fmt(pointMode ? r.points : r.amount)}</td>
                ${pointMode
                  ? `<td>${r.status === 'active' ? '正常' : '停用'}</td>`
                  : `<td class="r">${fmt(r.flow_count)}</td>`}
              </tr>`).join('')}
        </tbody>
      </table>
    </div>`

  const exeBlocks = executors.map(ex => {
    const groups = (ex.groups || []).map(g =>
      `<div class="grp">群 ${esc(g.group_id)}「${esc(g.group_name || '—')}」 · ${g.status === 'active' ? '正常' : '封禁'}
        · 群人数 ${fmt(g.qq_member_count)} · 建档会员 ${fmt(g.member_registered)}
        · 今日 ${fmt(g.today_game_count)} 局 / 抽水 ${fmt(g.today_fee)} 分</div>`).join('')
    const rate = (Number(ex.game_fee_rate || 0) / 10) + '%'
    return `
    <div class="exe">
      <div><b>${esc(ex.name)}</b> ${EXE_BADGE[ex.status] || ''}${ex.ban_reason ? ` <span class="muted">封禁原因：${esc(ex.ban_reason)}</span>` : ''}</div>
      <p class="meta">版本 ${esc(ex.version || '—')} · 操作员QQ ${esc(ex.admin_qq || '—')}
        · 最近心跳 ${esc((ex.last_heartbeat || '').slice(5) || '—')} · 费率 ${rate} · 群数 ${fmt(ex.group_count)}</p>
      <p class="meta">今日：${fmt(ex.today_game_count)} 局 · 抽水 <span class="fee">${fmt(ex.today_fee)}</span> 分 · 人次 ${fmt(ex.today_persons)}
        ｜ 近${ret}天：${fmt(ex.month_game_count)} 局 · 抽水 <span class="fee">${fmt(ex.month_fee)}</span> 分</p>
      ${groups}
    </div>`
  }).join('') || '<p class="muted">暂无执行器</p>'

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>红包积分经营日报 ${esc(day)}</title>
<style>
  body{font-family:"Microsoft YaHei",system-ui,sans-serif;font-size:14px;color:#333;margin:0;background:#f0f2f5}
  .wrap{max-width:1080px;margin:0 auto;padding:24px;background:#fff;min-height:100vh}
  h1{text-align:center;font-size:22px;margin:0 0 4px}
  .sub{text-align:center;color:#888;font-size:12px;margin:0 0 8px}
  h2{font-size:16px;margin:22px 0 10px;padding-left:8px;border-left:4px solid #fa8c16}
  h3{margin:6px 0 6px;font-size:14px}
  table{width:100%;border-collapse:collapse;margin-bottom:10px}
  td,th{border:1px solid #e8e8e8;padding:6px 10px;text-align:left;font-weight:400;font-size:13px}
  th{background:#fafafa}
  td.k{width:36%;background:#fafafa;color:#666}
  td.r,th.r{text-align:right}
  .pos{color:#52c41a}.neg{color:#cf1322}.fee{color:#fa8c16}
  .muted{color:#999;font-size:12px;line-height:1.8}
  .exe{margin:10px 0 16px;border:1px solid #eee;border-radius:6px;padding:10px 14px}
  .exe .meta{margin:6px 0;color:#555;font-size:13px}
  .grp{background:#fafafa;border:1px dashed #e8e8e8;padding:4px 10px;margin:6px 0;border-radius:4px;font-size:13px}
  .st{display:inline-block;font-size:12px;padding:1px 8px;border-radius:8px;margin-left:8px}
  .st.ok{color:#389e0d;background:#f6ffed;border:1px solid #b7eb8f}
  .st.no{color:#d46b08;background:#fff7e6;border:1px solid #ffd591}
  .st.bad{color:#cf1322;background:#fff1f0;border:1px solid #ffa39e}
  .st.or{color:#722ed1;background:#f9f0ff;border:1px solid #d3adf7}
  .rank-grid{display:flex;flex-wrap:wrap;gap:14px}
  .rank-grid .rc{flex:1 1 200px;min-width:210px}
  @media print{body{background:#fff}.wrap{padding:0}}
</style>
</head>
<body><div class="wrap">
  <h1>红包积分经营日报</h1>
  <p class="sub">统计日期 ${esc(day)} · 报表生成于 ${esc(genAt || '—')}（本文件为生成时点快照，刷新页面后可重新生成）</p>

  <h2>一、今日经营</h2>
  <table>
    ${pair('上分（手动充值）', `<span class="pos">${fmt(today.up)}</span> 分（${fmt(today.manual_count)} 笔资金流水）`)}
    ${pair('下分（手动提现）', `<span class="neg">${fmt(today.down)}</span> 分`)}
    ${pair('玩法流入（赢家结算）', `<span class="pos">${fmt(today.game_in)}</span> 分`)}
    ${pair('玩法流出（输家结算）', `<span class="neg">${fmt(today.game_out)}</span> 分`)}
    ${pair('抽水（房费收入）', `<span class="fee">${fmt(today.fee)}</span> 分`)}
    ${pair('对局数', `${fmt(today.game_count)} 局（异常 ${fmt(today.warn_games)} 局）`)}
    ${pair('参与玩家', `${fmt(today.players)} 人（事件人次 ${fmt(today.persons)}）`)}
    ${pair('玩法流水', `${fmt(today.game_flow_count)} 笔`)}
  </table>

  <h2>二、近 7 日趋势（今天在最上；上分/下分 = 手动资金口径）</h2>
  <table>
    <thead><tr><th>日期</th><th class="r">上分(分)</th><th class="r">下分(分)</th><th class="r">局数</th><th class="r">抽水(分)</th><th class="r">新增会员</th></tr></thead>
    <tbody>${trend.map(r => `<tr><td>${esc(r.date)}</td><td class="r pos">${fmt(r.up)}</td><td class="r neg">${fmt(r.down)}</td><td class="r">${fmt(r.game_count)}</td><td class="r fee">${fmt(r.fee)}</td><td class="r">${fmt(r.new_members)}</td></tr>`).join('')}</tbody>
  </table>

  <h2>三、今日玩法分布</h2>
  ${(today.play_stats || []).length === 0
    ? '<p class="muted">今日暂无对局</p>'
    : `<table><thead><tr><th>玩法</th><th class="r">局数</th><th class="r">抽水(分)</th><th class="r">人次</th><th class="r">异常局</th></tr></thead>
      <tbody>${today.play_stats.map(p => `<tr><td>${esc(p.play_name)}</td><td class="r">${fmt(p.game_count)}</td><td class="r fee">${fmt(p.fee)}</td><td class="r">${fmt(p.persons)}</td><td class="r">${fmt(p.warn_cnt)}</td></tr>`).join('')}</tbody></table>`}

  <h2>四、近 ${ret} 天游戏汇总（随保留期滑动，与自动清理口径一致）</h2>
  <table>
    ${pair('对局数', `${fmt(month.game_count)} 局`)}
    ${pair('抽水（房费收入）', `<span class="fee">${fmt(month.fee)}</span> 分`)}
    ${pair('参与人次', `${fmt(month.persons)} 人次`)}
    ${pair('异常局', `${fmt(month.warn_games)} 局`)}
  </table>

  <h2>五、当前存量（全生命周期累计）</h2>
  <table>
    ${pair('会员总数', `${fmt(totals.member_count)} 人（正常 ${fmt(totals.member_active)} · 停用 ${fmt(totals.member_disabled)} · 今日新增 ${fmt(totals.member_today_new)}）`)}
    ${pair('积分存量', `${fmt(totals.total_points)} 分`)}
    ${pair('累计上分 / 下分', `${fmt(totals.total_income)} / ${fmt(totals.total_outcome)} 分`)}
    ${pair('QQ群', `${fmt(totals.group_count)} 个（封禁 ${fmt(totals.group_banned)}）`)}
    ${pair('执行器', `${fmt(totals.executor_count)} 个（在线 ${fmt(totals.executor_online)} · 封禁 ${fmt(totals.executor_banned)}）`)}
    ${pair('操作员', `${fmt(totals.operator_count)} 人`)}
    ${pair('启用玩法', `${fmt(totals.play_count)} 个`)}
  </table>

  <h2>六、执行器与群明细</h2>
  ${exeBlocks}

  <h2>七、玩家排行</h2>
  <div class="rank-grid">
    ${rankTable('今日充值榜', '#52c41a', rk.up_today, false)}
    ${rankTable('今日提现榜', '#cf1322', rk.down_today, false)}
    ${rankTable('今日玩法赢家榜', '#52c41a', rk.win_today, false)}
    ${rankTable('今日玩法输家榜', '#cf1322', rk.lose_today, false)}
    ${rankTable('当前总积分榜', '#cf1322', rk.top_points, true)}
  </div>

  <h2>附：部署信息与口径说明</h2>
  <table>
    ${pair('服务器IP / 主机名', `${esc(dep.server_ip || '—')}（${esc(dep.host_name || '—')}）`)}
    ${pair('操作系统 / Java', `${esc(dep.os || '—')} / ${esc(dep.java_version || '—')}`)}
    ${pair('运行目录', esc(dep.base_dir || '—'))}
    ${pair('服务端口 / 环境', `${esc(dep.server_port || '—')}（${esc(dep.profile || '—')}）`)}
    ${pair('数据库地址', `${esc(dep.db_host || '—')} / ${esc(dep.db_name || '—')}`)}
    ${pair('数据库版本', esc(dep.db_version || '—'))}
  </table>
  <p class="muted">
    口径：上分/下分 = 手动资金（充值/提现）；玩法流入/流出 = 玩法对局结算的赢家入账 / 输家扣账；抽水 = 每局净额合计（−Σtotal_delta，即房费收入）。<br>
    积分流水永久保留；游戏记录与操作日志按保留期清理（当前 ${ret} 天）。单位：分（1 分 = 1 分）。本文件由管理后台「报表展示」生成。
  </p>
</div></body>
</html>`
}

/**
 * 报表展示（只读，非技术口径）：
 * 总览存量 / 今日四口径(手动充值·提现·玩法赢·玩法输)+抽水局数 / 近7日趋势 /
 * 近N天游戏汇总 / 执行器与群明细(展开) / 玩家排行。数据一次性取回，手动刷新。
 */
export default function Report() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/report/overview')
      setData(res.data?.data || null)
    } catch (e) {
      message.error(e.response?.data?.message || '报表加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /** 把当前数据快照生成为「每日最终统计日报」HTML 并触发浏览器下载（文件名带统计日期） */
  const downloadReport = () => {
    if (!data) return
    const day = (data.generated_at || new Date().toISOString()).slice(0, 10)
    // 前缀 BOM：保证 Windows 记事本/Excel 双击打开不乱码（HTML 本身带 charset 声明）
    const blob = new Blob([String.fromCharCode(0xFEFF) + buildDailyHtml(data)], { type: 'text/html;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `红包积分经营日报-${day}.html`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    message.success(`已生成日报 HTML（${day}），开始下载`)
  }

  const totals = data?.totals || {}
  const today = data?.today || {}
  const deployment = data?.deployment || {}

  const amountCols = (color) => [
    { title: '名次', key: 'rank', width: 56, render: (_, __, i) => <b style={{ color: i < 3 ? '#fa8c16' : undefined }}>{i + 1}</b> },
    { title: '昵称', dataIndex: 'nickname', ellipsis: true, render: (v, r) => <span>{v || '—'}<span style={{ color: '#999' }}> ({r.qq})</span></span> },
    { title: '金额(分)', dataIndex: 'amount', align: 'right', width: 110, render: v => <span style={{ color }}>{fmt(v)}</span> },
    { title: '笔数', dataIndex: 'flow_count', align: 'right', width: 64, render: v => fmt(v) },
  ]

  const rankTables = [
    { key: 'up_today', title: '今日充值榜', color: '#52c41a', rows: data?.rankings?.up_today || [] },
    { key: 'down_today', title: '今日提现榜', color: '#cf1322', rows: data?.rankings?.down_today || [] },
    { key: 'win_today', title: '今日玩法赢家榜', color: '#52c41a', rows: data?.rankings?.win_today || [] },
    { key: 'lose_today', title: '今日玩法输家榜', color: '#cf1322', rows: data?.rankings?.lose_today || [] },
  ]

  const executorColumns = [
    { title: '执行器', dataIndex: 'name', width: 150, render: (v, r) => (
      <Space size={6}>
        <span>{v || '—'}</span>
        <Tag color={EXE_STATUS[r.status]?.color}>{EXE_STATUS[r.status]?.text}</Tag>
        {r.ban_reason && <Tooltip title={r.ban_reason}><Tag color="red">封禁原因</Tag></Tooltip>}
      </Space>
    ) },
    { title: '版本', dataIndex: 'version', width: 110, render: v => v || '—' },
    { title: '操作员QQ', dataIndex: 'admin_qq', width: 100, render: v => v || '—' },
    { title: '最近心跳', dataIndex: 'last_heartbeat', width: 150, render: v => v ? v.slice(5) : '—' },
    { title: '费率', dataIndex: 'game_fee_rate', width: 70, align: 'right', render: v => Number(v || 0) / 10 + '%' },
    { title: '群数', dataIndex: 'group_count', width: 60, align: 'right' },
    { title: '今日局数', dataIndex: 'today_game_count', width: 80, align: 'right' },
    { title: '今日抽水', dataIndex: 'today_fee', width: 100, align: 'right', render: v => <span style={{ color: '#fa8c16' }}>{fmt(v)}</span> },
    { title: '今日人次', dataIndex: 'today_persons', width: 80, align: 'right' },
    { title: `近${data?.retention_days || 30}天局数`, dataIndex: 'month_game_count', width: 100, align: 'right' },
    { title: `近${data?.retention_days || 30}天抽水`, dataIndex: 'month_fee', width: 110, align: 'right', render: v => <span style={{ color: '#fa8c16' }}>{fmt(v)}</span> },
  ]

  const groupColumns = [
    { title: '群号', dataIndex: 'group_id', width: 110 },
    { title: '群名称', dataIndex: 'group_name', ellipsis: true, render: v => v || '—' },
    { title: '状态', dataIndex: 'status', width: 80, render: v =>
      <Tag color={v === 'active' ? 'green' : 'red'}>{v === 'active' ? '正常' : '封禁'}</Tag> },
    { title: '群人数', dataIndex: 'qq_member_count', width: 80, align: 'right', render: v => fmt(v) },
    { title: '建档会员', dataIndex: 'member_registered', width: 90, align: 'right', render: v => fmt(v) },
    { title: '今日局数', dataIndex: 'today_game_count', width: 80, align: 'right' },
    { title: '今日抽水', dataIndex: 'today_fee', width: 100, align: 'right', render: v => <span style={{ color: '#fa8c16' }}>{fmt(v)}</span> },
  ]

  return (
    <div>
      {/* 顶部：标题 + 刷新 + 口径提示 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
        <b style={{ fontSize: 16 }}><BarChartOutlined /> 报表展示</b>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
        <Button size="small" type="primary" icon={<DownloadOutlined />} disabled={!data} onClick={downloadReport}>下载报表</Button>
        <span style={{ ...gray, marginLeft: 'auto' }}>点击下载：当日全部统计的最终日报 HTML（可打印/留档，文件名带日期）</span>
        <span style={gray}>生成时间：{data?.generated_at || '—'}　·　游戏局数/抽水等数据按保留期保留近 {data?.retention_days ?? 30} 天；积分单位：分</span>
      </div>

      {/* 一、总览（现状存量） */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={4}><Card size="small">
          <Statistic title="会员总数" value={totals.member_count || 0} suffix="人" />
          <Note>正常 {fmt(totals.member_active)} · 停用 {fmt(totals.member_disabled)} · 今日新增 {fmt(totals.member_today_new)}</Note>
        </Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="积分存量" value={totals.total_points || 0} valueStyle={{ color: '#cf1322' }} suffix="分" />
          <Note>会员当前积分合计（潜在提现规模）</Note>
        </Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="QQ群" value={totals.group_count || 0} suffix="个" />
          <Note>封禁 {fmt(totals.group_banned)} 个</Note>
        </Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="执行器" value={totals.executor_count || 0} suffix="个" />
          <Note>在线 {fmt(totals.executor_online)} · 封禁 {fmt(totals.executor_banned)}</Note>
        </Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="操作员" value={totals.operator_count || 0} suffix="人" />
          <Note>管理 QQ（agent 心跳自动登记）</Note>
        </Card></Col>
        <Col span={4}><Card size="small">
          <Statistic title="启用玩法" value={totals.play_count || 0} suffix="个" />
          <Note>全生命周期累计上分 {fmt(totals.total_income)} / 下分 {fmt(totals.total_outcome)}（分）</Note>
        </Card></Col>
      </Row>

      {/* 二、今日：手动资金 与 玩法结算 分开 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={3}><Card size="small">
          <Statistic title="今日上分（手动充值）" value={today.up || 0} valueStyle={{ color: '#52c41a' }} suffix="分" />
          <Note>会员充值入金，{fmt(today.manual_count)} 笔</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日下分（手动提现）" value={today.down || 0} valueStyle={{ color: '#cf1322' }} suffix="分" />
          <Note>会员提现出金</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日玩法流入（赢）" value={today.game_in || 0} valueStyle={{ color: '#52c41a' }} suffix="分" />
          <Note>对局赢家结算入账</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日玩法流出（输）" value={today.game_out || 0} valueStyle={{ color: '#cf1322' }} suffix="分" />
          <Note>对局输家结算扣账</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日抽水" value={today.fee || 0} valueStyle={{ color: '#fa8c16' }} suffix="分" />
          <Note>每局净额合计 = 房费收入</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日局数" value={today.game_count || 0} suffix="局" />
          <Note>异常 {fmt(today.warn_games)} 局（点开可查）</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日参与玩家" value={today.players || 0} suffix="人" />
          <Note>按事件去重；人次 {fmt(today.persons)}</Note>
        </Card></Col>
        <Col span={3}><Card size="small">
          <Statistic title="今日玩法流水" value={today.game_flow_count || 0} suffix="笔" />
          <Note>与手动资金（上排卡片）互不掺和</Note>
        </Card></Col>
      </Row>

      {/* 三、近7日趋势 + 玩法分布 + 近N天汇总 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={15}>
          <Card size="small" title="近 7 日趋势（每日 00:00 起；上分/下分=手动资金口径）">
            <Table
              rowKey="date" size="small" pagination={false} loading={loading}
              dataSource={data?.trend || []}
              columns={[
                { title: '日期', dataIndex: 'date', width: 110 },
                { title: '上分(分)', dataIndex: 'up', align: 'right', render: v => <span style={{ color: '#52c41a' }}>{fmt(v)}</span> },
                { title: '下分(分)', dataIndex: 'down', align: 'right', render: v => <span style={{ color: '#cf1322' }}>{fmt(v)}</span> },
                { title: '局数', dataIndex: 'game_count', align: 'right', render: v => fmt(v) },
                { title: '抽水(分)', dataIndex: 'fee', align: 'right', render: v => <span style={{ color: '#fa8c16' }}>{fmt(v)}</span> },
                { title: '新增会员', dataIndex: 'new_members', align: 'right', render: v => fmt(v) },
              ]}
            />
          </Card>
        </Col>
        <Col span={9}>
          <Card size="small" title="今日对局分布">
            {(today.play_stats || []).length === 0
              ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="今日暂无对局" style={{ margin: '12px 0' }} />
              : <Table
                  rowKey="play_name" size="small" pagination={false}
                  dataSource={today.play_stats}
                  columns={[
                    { title: '玩法', dataIndex: 'play_name', ellipsis: true },
                    { title: '局数', dataIndex: 'game_count', align: 'right', width: 64 },
                    { title: '抽水', dataIndex: 'fee', align: 'right', width: 90, render: v => <span style={{ color: '#fa8c16' }}>{fmt(v)}</span> },
                    { title: '人次', dataIndex: 'persons', align: 'right', width: 70 },
                    { title: '异常', dataIndex: 'warn_cnt', align: 'right', width: 64, render: v => (v || 0) > 0 ? <span style={{ color: '#cf1322' }}>{v}</span> : '—' },
                  ]}
                />}
          </Card>
          <Card size="small" style={{ marginTop: 16 }} title={`近 ${data?.retention_days ?? 30} 天游戏汇总（随保留期滑动）`}>
            <Row gutter={16}>
              <Col span={6}><Statistic title="局数" value={data?.month?.game_count || 0} /></Col>
              <Col span={6}><Statistic title="抽水(分)" value={data?.month?.fee || 0} valueStyle={{ color: '#fa8c16' }} /></Col>
              <Col span={6}><Statistic title="人次" value={data?.month?.persons || 0} /></Col>
              <Col span={6}><Statistic title="异常局" value={data?.month?.warn_games || 0} valueStyle={{ color: data?.month?.warn_games ? '#cf1322' : undefined }} /></Col>
            </Row>
          </Card>
        </Col>
      </Row>

      {/* 四、执行器与群明细（展开看群） */}
      <Card
        size="small" style={{ marginBottom: 16 }}
        title={<>执行器与群明细<Note style={{ display: 'inline', marginLeft: 8 }}>展开行可看该执行器每个群的今日数据；「未绑定执行器」行为无人管理的群</Note></>}
      >
        <Table
          rowKey={r => r.id}
          size="small"
          loading={loading}
          dataSource={data?.executors || []}
          columns={executorColumns}
          expandable={{
            expandedRowRender: r => (
              <Table rowKey="group_id" size="small" pagination={false}
                     dataSource={r.groups || []} columns={groupColumns} />
            ),
          }}
          pagination={{ pageSize: 10, showTotal: t => `共 ${t} 行` }}
        />
      </Card>

      {/* 五、玩家排行 */}
      <Row gutter={[16, 16]}>
        {rankTables.map(r => (
          <Col span={6} key={r.key}>
            <Card size="small" title={`${r.title} Top10`}>
              <Table rowKey={qq => qq} size="small" pagination={false}
                     dataSource={r.rows} columns={amountCols(r.color)}
                     locale={{ emptyText: '暂无数据' }} />
            </Card>
          </Col>
        ))}
        <Col span={12}>
          <Card size="small" title="当前总积分榜 Top10">
            <Table
              rowKey={r => r.qq} size="small" pagination={false}
              dataSource={data?.rankings?.top_points || []}
              columns={[
                { title: '名次', key: 'rank', width: 56, render: (_, __, i) => <b style={{ color: i < 3 ? '#fa8c16' : undefined }}>{i + 1}</b> },
                { title: '昵称', dataIndex: 'nickname', ellipsis: true, render: (v, r) => <span>{v || '—'}<span style={{ color: '#999' }}> ({r.qq})</span></span> },
                { title: '积分(分)', dataIndex: 'points', align: 'right', width: 110, render: v => <b style={{ color: '#cf1322' }}>{fmt(v)}</b> },
                { title: '状态', dataIndex: 'status', width: 70, render: v =>
                  <Tag color={v === 'active' ? 'green' : 'default'}>{v === 'active' ? '正常' : '停用'}</Tag> },
              ]}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
        {/* 总积分榜右侧空位：当前部署信息（增值报表客户可据此核对部署环境） */}
        <Col span={12}>
          <Card size="small" title="当前部署信息">
            <Descriptions size="small" column={1} bordered
              items={[
                { key: 'ip', label: '服务器IP', children: deployment.server_ip || '—' },
                { key: 'host', label: '主机名', children: deployment.host_name || '—' },
                { key: 'os', label: '操作系统', children: deployment.os || '—' },
                { key: 'java', label: 'Java 版本', children: deployment.java_version || '—' },
                { key: 'db', label: '数据库', children: `${deployment.db_host || '—'} / ${deployment.db_name || '—'}` },
                { key: 'dbver', label: '数据库版本', children: deployment.db_version || '—' },
                { key: 'port', label: '服务端口', children: `${deployment.server_port || '—'}（${deployment.profile || '-'} 环境）` },
                { key: 'dir', label: '运行目录', children: deployment.base_dir || '—' },
              ]}
            />
            <Note>流水永久保留；游戏/操作数据按保留期清理（当前 {data?.retention_days ?? 30} 天）；点「下载报表」可生成当天最终统计日报 HTML</Note>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
