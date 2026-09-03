import React, { useEffect, useState, useCallback } from 'react'
import { Card, Table, Input, Select, Button, Space, Modal, Form, InputNumber, message,
         Popconfirm, Tag, Drawer, Row, Col, Statistic } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, RiseOutlined, FallOutlined,
         HistoryOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import api from '../api'

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

  const loadRecords = useCallback(async (page) => {
    setRecordLoading(true)
    try {
      const res = await api.get('/api/admin/point-records', { params: { qq: recordQq, page, size: 20 } })
      setRecords(res.data?.data?.list || [])
      setRecordTotal(res.data?.data?.total || 0)
      setRecordPage(page)
    } finally {
      setRecordLoading(false)
    }
  }, [recordQq])

  const openRecord = qq => {
    setRecordQq(qq)
    setRecordOpen(true)
    loadRecords(1)
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
    { title: '来源群', dataIndex: 'group_id', width: 100 },
    { title: '状态', dataIndex: 'status', width: 80, render: v =>
        <Tag color={v === 'active' ? 'green' : 'default'}>{v === 'active' ? '正常' : '停用'}</Tag> },
    { title: '备注', dataIndex: 'note', ellipsis: true },
    {
      title: '操作', width: 250, fixed: 'right',
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

        <Table rowKey="id" columns={columns} dataSource={list} loading={loading}
               size="middle" scroll={{ x: 1100 }} pagination={{ pageSize: 20 }} />
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

      {/* 流水抽屉 */}
      <Drawer title={`积分流水 — ${recordQq}`} width={680} open={recordOpen} onClose={() => setRecordOpen(false)}>
        <Table rowKey="id" size="small" loading={recordLoading} dataSource={records}
               pagination={{ current: recordPage, total: recordTotal, pageSize: 20, showTotal: t => `共 ${t} 条`,
                 onChange: loadRecords }}
               columns={[
                 { title: '变动', dataIndex: 'delta', width: 90, render: v =>
                     <span style={{ color: v > 0 ? '#52c41a' : '#ff4d4f', fontWeight: 600 }}>{v > 0 ? '+' : ''}{v}</span> },
                 { title: '类型', dataIndex: 'type', width: 90, render: v =>
                     <Tag color={v === 'INCOME' ? 'green' : 'red'}>{v === 'INCOME' ? '上分' : '下分'}</Tag> },
                 { title: '原因', dataIndex: 'reason', ellipsis: true },
                 { title: '操作人', dataIndex: 'operator', width: 110 },
                 { title: '业务单号', dataIndex: 'biz_no', width: 140, ellipsis: true },
                 { title: '时间', dataIndex: 'created_at', width: 170 },
               ]} />
      </Drawer>
    </div>
  )
}
