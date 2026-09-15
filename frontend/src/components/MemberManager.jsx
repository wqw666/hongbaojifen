import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Select, Button, Space, Modal, Form, InputNumber, message,
         Popconfirm, Tag, Drawer, Row, Col, Statistic, Tooltip, Segmented } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, RiseOutlined, FallOutlined,
         HistoryOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

// 积分来源：manual 后台手动 / approve 群内审批 / game 玩法结算（与后端 V1.0.14 一致）
const SOURCE_LABEL = { manual: '后台手动', approve: '群内审批', game: '玩法结算' }

// 流水类型标签：来源 × 收支 细分 7 种（flow_amount≠0 且 delta=0 的是「流水」行）
function typeTag (row) {
  const src = row.source || 'manual'
  const flowOnly = row.type === 'FLOW' || (!row.delta && row.flow_amount)
  if (flowOnly) return <Tag color="blue">流水</Tag>
  if (src === 'game') return <Tag color="geekblue">{row.delta > 0 ? '结算得分' : '结算失分'}</Tag>
  if (src === 'approve') return <Tag color="cyan">{row.delta > 0 ? '审批上分' : '审批下分'}</Tag>
  return <Tag color={row.delta > 0 ? 'green' : 'red'}>{row.delta > 0 ? '手动上分' : '手动下分'}</Tag>
}

export default function MemberManager() {
  const [list, setList] = useState([])
  const [stats, setStats] = useState({})
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState()
  const [editForm] = Form.useForm()
  const [pointForm] = Form.useForm()

  // 新建/编辑
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  // 上下分
  const [pointOpen, setPointOpen] = useState(false)
  const [pointRow, setPointRow] = useState(null)
  const [pointMode, setPointMode] = useState('up')
  // 流水抽屉
  const [recordOpen, setRecordOpen] = useState(false)
  const [recordQq, setRecordQq] = useState('')
  const [records, setRecords] = useState([])
  const [recordLoading, setRecordLoading] = useState(false)
  const [recordPage, setRecordPage] = useState(1)
  const [recordTotal, setRecordTotal] = useState(0)
  const [recordSource, setRecordSource] = useState('')       // '' 全部 / manual / approve / game
  const [recordSummary, setRecordSummary] = useState({})     // 全量口径合计（不随筛选变）

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/members', { params: { keyword, status } })
      setList(res.data?.data || [])
      const s = await api.get('/api/admin/members/stats')
      setStats(s.data?.data || {})
    } finally {
      setLoading(false)
    }
  }, [keyword, status])

  useEffect(() => { load() }, [load])

  const loadRecords = useCallback(async (page, source) => {
    setRecordLoading(true)
    try {
      const params = { qq: recordQq, page, size: 20 }
      if (source) params.source = source
      const res = await api.get('/api/admin/point-records', { params })
      setRecords(res.data?.data?.list || [])
      setRecordTotal(res.data?.data?.total || 0)
      setRecordSummary(res.data?.data?.summary || {})
      setRecordPage(page)
    } finally {
      setRecordLoading(false)
    }
  }, [recordQq])

  const openRecord = qq => {
    setRecordQq(qq)
    setRecordSource('')
    setRecordOpen(true)
    loadRecords(1, '')
  }

  const openPoint = (row, mode) => {
    setPointRow(row)
    setPointMode(mode)
    setPointOpen(true)
  }

  const submitPoint = async values => {
    const delta = pointMode === 'up' ? values.delta : -values.delta
    await api.post(`/api/admin/members/${pointRow.id}/points`, { delta, reason: values.reason })
    message.success(pointMode === 'up' ? '上分成功' : '下分成功')
    setPointOpen(false)
    load()
  }

  const submitEdit = async values => {
    if (editRow) {
      await api.put(`/api/admin/members/${editRow.id}`, values)
    } else {
      await api.post('/api/admin/members', values)
    }
    message.success('已保存')
    setEditOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/members/${row.id}`)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: 'QQ号', dataIndex: 'qq', width: 110 },
    { title: '昵称', dataIndex: 'nickname', ellipsis: true },
    { title: '当前积分', dataIndex: 'points', width: 90, sorter: (a, b) => a.points - b.points },
    { title: '累计上分', dataIndex: 'total_income', width: 90 },
    { title: '累计下分', dataIndex: 'total_outcome', width: 90 },
    // 手动 = 后台手动 + 群内审批（点标题下的 tooltip 看拆分）；玩法 = 对局结算
    { title: '手动上分', dataIndex: 'manual_income', width: 100,
      render: (v, row) => (
        <Tooltip title={`后台手动 ${v || 0} + 群内审批 ${row.approve_income || 0}`}>
          <span>{(v || 0) + (row.approve_income || 0)}</span>
        </Tooltip>) },
    { title: '手动下分', dataIndex: 'manual_outcome', width: 100,
      render: (v, row) => (
        <Tooltip title={`后台手动 ${v || 0} + 群内审批 ${row.approve_outcome || 0}`}>
          <span>{(v || 0) + (row.approve_outcome || 0)}</span>
        </Tooltip>) },
    { title: '玩法得分', dataIndex: 'game_income', width: 90, render: v => v || 0 },
    { title: '玩法失分', dataIndex: 'game_outcome', width: 90, render: v => v || 0 },
    { title: '来源群', dataIndex: 'group_id', width: 100, render: v => v || '—' },
    { title: '注册人QQ', dataIndex: 'registrar_qq', width: 100, render: v => v || '—' },
    { title: '状态', dataIndex: 'status', width: 80, render: v =>
        <Tag color={v === 'active' ? 'green' : 'default'}>{v === 'active' ? '正常' : '停用'}</Tag> },
    { title: '备注', dataIndex: 'note', width: 90, ellipsis: true },
    {
      title: '操作', width: 310, fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" type="primary" icon={<RiseOutlined />} onClick={() => openPoint(row, 'up')}>上分</Button>
          <Button size="small" danger icon={<FallOutlined />} onClick={() => openPoint(row, 'down')}>下分</Button>
          <Button size="small" icon={<HistoryOutlined />} onClick={() => openRecord(row.qq)}>流水</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => { setEditRow(row); setEditOpen(true) }}>编辑</Button>
          <Popconfirm title={`确认删除会员 ${row.qq}？`} onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="会员总数" value={stats.member_count || 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="总积分" value={stats.total_points || 0} valueStyle={{ color: '#ff4d4f' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="上分次数" value={stats.income_count || 0} valueStyle={{ color: '#52c41a' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="下分次数" value={stats.outcome_count || 0} valueStyle={{ color: '#fa8c16' }} /></Card></Col>
      </Row>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input placeholder="QQ号/昵称" allowClear style={{ width: 200 }} value={keyword}
                 onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
          <Select placeholder="状态" allowClear style={{ width: 120 }} value={status} onChange={setStatus}
                  options={[{ value: 'active', label: '正常' }, { value: 'disabled', label: '停用' }]} />
          <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); setStatus(undefined) }}>重置</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditRow(null); setEditOpen(true) }}>新增会员</Button>
        </Space>

        <ResizableTable rowKey="id" columns={columns} dataSource={list} loading={loading}
               size="middle" scroll={{ x: 1500 }} pagination={{ pageSize: 20 }} />
      </Card>

      {/* 新建/编辑 */}
      <Modal title={editRow ? '编辑会员' : '新增会员'} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => editForm.submit()} destroyOnClose>
        <Form form={editForm} onFinish={submitEdit} layout="vertical" initialValues={editRow || {}}>
          <Form.Item name="qq" label="QQ号" rules={[{ required: true, message: '请输入QQ号' }]}>
            <Input disabled={!!editRow} placeholder="会员QQ号（唯一标识）" />
          </Form.Item>
          <Form.Item name="nickname" label="昵称"><Input placeholder="会员昵称" /></Form.Item>
          <Form.Item name="group_id" label="来源群号"><Input placeholder="QQ群号" /></Form.Item>
          <Form.Item name="note" label="备注"><Input.TextArea rows={2} placeholder="备注信息" /></Form.Item>
          {editRow && (
            <Form.Item name="status" label="状态" initialValue="active">
              <Select options={[{ value: 'active', label: '正常' }, { value: 'disabled', label: '停用' }]} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* 上下分 */}
      <Modal title={`${pointMode === 'up' ? '上分' : '下分'} — ${pointRow?.qq || ''}（当前积分 ${pointRow?.points ?? ''}）`}
             open={pointOpen} onCancel={() => setPointOpen(false)} onOk={() => pointForm.submit()} destroyOnClose>
        <Form form={pointForm} onFinish={submitPoint} layout="vertical">
          <Form.Item name="delta" label={pointMode === 'up' ? '上分数量' : '下分数量'}
                     rules={[{ required: true, message: '请输入数量' }]}>
            <InputNumber min={1} max={99999999} style={{ width: '100%' }} placeholder="正整数" />
          </Form.Item>
          <Form.Item name="reason" label="原因" rules={[{ required: true, message: '请输入原因' }]}>
            <Input placeholder="如：签到奖励、红包活动、兑换消耗" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 流水抽屉：合计卡（全量口径）+ 来源筛选 + 类型细分 */}
      <Drawer title={`积分流水 — ${recordQq}`} width={860} open={recordOpen} onClose={() => setRecordOpen(false)}>
        <Card size="small" style={{ marginBottom: 12 }}>
          <Row gutter={8}>
            <Col span={5}><Statistic title="手动上分" value={(recordSummary.manual_income || 0) + (recordSummary.approve_income || 0)}
                                    valueStyle={{ color: '#52c41a', fontSize: 18 }} /></Col>
            <Col span={5}><Statistic title="手动下分" value={(recordSummary.manual_outcome || 0) + (recordSummary.approve_outcome || 0)}
                                    valueStyle={{ color: '#fa8c16', fontSize: 18 }} /></Col>
            <Col span={4}><Statistic title="玩法得分" value={recordSummary.game_income || 0}
                                    valueStyle={{ color: '#2f54eb', fontSize: 18 }} /></Col>
            <Col span={4}><Statistic title="玩法失分" value={recordSummary.game_outcome || 0}
                                    valueStyle={{ color: '#ff4d4f', fontSize: 18 }} /></Col>
            <Col span={6}><Statistic title="玩法流水额" value={recordSummary.game_flow || 0}
                                    valueStyle={{ fontSize: 18 }} /></Col>
          </Row>
          <div style={{ marginTop: 8, color: '#8c8c8c', fontSize: 12 }}>
            手动上/下分 = 后台手动 + 群内审批（后台手动 {recordSummary.manual_income || 0} / {recordSummary.manual_outcome || 0}；
            群内审批 {recordSummary.approve_income || 0} / {recordSummary.approve_outcome || 0}）；以上为全量合计，不随下方筛选变化
          </div>
        </Card>

        <Segmented style={{ marginBottom: 12 }} value={recordSource}
                   onChange={v => { setRecordSource(v); loadRecords(1, v) }}
                   options={[{ label: '全部', value: '' }, { label: '手动', value: 'manual' },
                             { label: '审批', value: 'approve' }, { label: '玩法', value: 'game' }]} />

        <ResizableTable rowKey="id" size="small" loading={recordLoading} dataSource={records}
               pagination={{ current: recordPage, total: recordTotal, pageSize: 20, showTotal: t => `共 ${t} 条`,
                 onChange: p => loadRecords(p, recordSource) }}
               columns={[
                 { title: '变动', dataIndex: 'delta', width: 90, render: v =>
                     <span style={{ color: v > 0 ? '#52c41a' : v < 0 ? '#ff4d4f' : '#8c8c8c', fontWeight: 600 }}>
                       {v > 0 ? '+' : ''}{v}
                     </span> },
                 { title: '类型', width: 100, render: (_, row) => typeTag(row) },
                 { title: '来源', dataIndex: 'source', width: 90,
                   render: v => SOURCE_LABEL[v || 'manual'] },
                 { title: '流水', dataIndex: 'flow_amount', width: 80,
                   render: (v, row) => (v && v !== row.delta) ? v : '—' },
                 { title: '原因', dataIndex: 'reason', ellipsis: true },
                 { title: '操作人', dataIndex: 'operator', width: 110 },
                 { title: '业务单号', dataIndex: 'biz_no', width: 140, ellipsis: true },
                 { title: '时间', dataIndex: 'created_at', width: 170 },
               ]} />
      </Drawer>
    </div>
  )
}
