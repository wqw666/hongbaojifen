import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Select, Button, Space, Modal, Form, message, Popconfirm, Tag, Tooltip } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
         StopOutlined, CheckCircleOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

export default function QqGroupManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState()
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  // 封禁：仅封禁/封禁并重置由执行器页承载，群只有 封禁(带原因)/解封
  const [banRow, setBanRow] = useState(null)
  const [banForm] = Form.useForm()
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/qq-groups', { params: { keyword, status } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [keyword, status])

  useEffect(() => { load() }, [load])

  const submit = async values => {
    if (editRow) {
      await api.put(`/api/admin/qq-groups/${editRow.id}`, values)
    } else {
      await api.post('/api/admin/qq-groups', values)
    }
    message.success('已保存')
    setEditOpen(false)
    load()
  }

  const submitBan = async values => {
    await api.post(`/api/admin/qq-groups/${banRow.id}/ban`, values)
    message.success('已封禁：该群停玩停同步，agent 上报的群信息不再生效')
    setBanRow(null)
    load()
  }

  const unban = async row => {
    await api.post(`/api/admin/qq-groups/${row.id}/unban`)
    message.success('已解封')
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/qq-groups/${row.id}`)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: '群号', dataIndex: 'group_id', width: 110 },
    { title: '群名称', dataIndex: 'group_name', ellipsis: true },
    { title: '群创建时间', dataIndex: 'create_time', width: 160, render: v => v || '—' },
    { title: '群主QQ', dataIndex: 'owner_qq', width: 100, render: v => v || '—' },
    { title: '管理员QQ', dataIndex: 'admin_qqs', width: 140, ellipsis: true, render: v => v || '—' },
    { title: '群人数', dataIndex: 'member_count', width: 80 },
    {
      title: '状态', dataIndex: 'status', width: 110,
      render: (v, row) => v === 'banned'
        ? <Tooltip title={row.ban_reason ? `封禁原因：${row.ban_reason}` : '已封禁'}>
            <Tag color="red">封禁</Tag>
          </Tooltip>
        : <Tag color="green">正常</Tag>,
    },
    { title: '备注', dataIndex: 'note', ellipsis: true },
    {
      title: '操作', width: 190, fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          {row.status === 'banned'
            ? <Button size="small" type="primary" ghost icon={<CheckCircleOutlined />} onClick={() => unban(row)}>解封</Button>
            : <Button size="small" danger icon={<StopOutlined />}
                      onClick={() => { setBanRow(row); banForm.resetFields(); }}>封禁</Button>}
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => { setEditRow(row); form.setFieldsValue(row); setEditOpen(true) }}>编辑</Button>
          <Popconfirm title="确认删除？" onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="QQ群管理">
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="群号/群名" allowClear style={{ width: 200 }} value={keyword}
               onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
        <Select placeholder="状态" allowClear style={{ width: 120 }} value={status} onChange={setStatus}
                options={[{ value: 'active', label: '正常' }, { value: 'banned', label: '封禁' }]} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); setStatus(undefined) }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { setEditRow(null); form.resetFields(); setEditOpen(true) }}>新增群</Button>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      <Modal title={editRow ? '编辑群' : '新增群'} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submit} layout="vertical">
          <Form.Item name="group_id" label="群号" rules={[{ required: true, message: '请输入群号' }]}>
            <Input disabled={!!editRow} placeholder="QQ群号" />
          </Form.Item>
          <Form.Item name="group_name" label="群名称" rules={[{ required: true, message: '请输入群名称' }]}>
            <Input placeholder="群名称" />
          </Form.Item>
          <Form.Item name="owner_qq" label="群主QQ"><Input placeholder="群主QQ号" /></Form.Item>
          <Form.Item name="admin_qqs" label="管理员QQ"><Input placeholder="多个用逗号分隔" /></Form.Item>
          <Form.Item name="member_count" label="群人数"><Input placeholder="当前群人数" /></Form.Item>
          <Form.Item name="note" label="备注"><Input placeholder="备注" /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`封禁群 — ${banRow?.group_name || ''}`} open={!!banRow} onCancel={() => setBanRow(null)}
             onOk={() => banForm.submit()} destroyOnClose>
        <Form form={banForm} onFinish={submitBan} layout="vertical">
          <Form.Item name="reason" label="封禁原因" rules={[{ required: true, message: '请输入封禁原因' }]}>
            <Input.TextArea rows={3} placeholder="如：涉嫌违规、群主撤出等（封禁后该群停玩停同步，可随时解封）" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
