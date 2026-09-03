import React, { useEffect, useState, useCallback } from 'react'
import { Card, Table, Input, Select, Button, Space, Modal, Form, message, Popconfirm, Tag, Typography, Alert } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, EditOutlined, DeleteOutlined, KeyOutlined } from '@ant-design/icons'
import api from '../api'

const { Text } = Typography

export default function ExecutorManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState()
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [newToken, setNewToken] = useState('')
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/executors', { params: { keyword, status } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [keyword, status])

  useEffect(() => { load() }, [load])

  const submit = async values => {
    let res
    if (editRow) {
      res = await api.put(`/api/admin/executors/${editRow.id}`, values)
    } else {
      res = await api.post('/api/admin/executors', values)
      setNewToken(res.data?.data?.token_plain || '')
    }
    message.success(res.data?.message || '已保存')
    setEditOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/executors/${row.id}`)
    message.success('已删除')
    load()
  }

  const resetToken = async row => {
    const res = await api.post(`/api/admin/executors/${row.id}/reset-token`)
    setNewToken(res.data?.token || '')
    load()
  }

  const columns = [
    { title: '名称', dataIndex: 'name', width: 130 },
    { title: '状态', dataIndex: 'status', width: 90, render: v =>
        <Tag color={v === 'online' ? 'green' : 'default'}>{v === 'online' ? '在线' : '离线'}</Tag> },
    { title: '版本', dataIndex: 'version', width: 100 },
    { title: '主机', dataIndex: 'host', width: 150, ellipsis: true },
    { title: '负责群号', dataIndex: 'group_id', width: 110 },
    { title: '最后心跳', dataIndex: 'last_heartbeat', width: 170 },
    { title: 'Token', dataIndex: 'token', width: 110, ellipsis: true,
      render: v => <Text code copyable={{ text: v }} style={{ fontSize: 12 }}>{v.slice(0, 8)}…</Text> },
    {
      title: '操作', width: 190,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => { setEditRow(row); form.setFieldsValue(row); setEditOpen(true) }}>编辑</Button>
          <Button size="small" icon={<KeyOutlined />} onClick={() => resetToken(row)}>重置Token</Button>
          <Popconfirm title="确认删除？" onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="执行器管理">
      {newToken && (
        <Alert style={{ marginBottom: 16 }} type="warning" showIcon closable onClose={() => setNewToken('')}
               message="执行器 Token（仅显示一次，请立即复制保存）"
               description={<Text copyable style={{ fontSize: 14 }}>{newToken}</Text>} />
      )}
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="名称/主机搜索" allowClear style={{ width: 200 }} value={keyword}
               onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
        <Select placeholder="状态" allowClear style={{ width: 120 }} value={status} onChange={setStatus}
                options={[{ value: 'online', label: '在线' }, { value: 'offline', label: '离线' }]} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); setStatus(undefined) }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { setEditRow(null); form.resetFields(); setEditOpen(true) }}>新增执行器</Button>
      </Space>

      <Table rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      <Modal title={editRow ? '编辑执行器' : '新增执行器'} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submit} layout="vertical">
          <Form.Item name="name" label="执行器名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：群A-1号执行器" />
          </Form.Item>
          <Form.Item name="group_id" label="负责群号"><Input placeholder="QQ群号" /></Form.Item>
          <Form.Item name="version" label="程序版本"><Input placeholder="如：1.0.0" /></Form.Item>
          <Form.Item name="status" label="状态" initialValue="offline">
            <Select options={[{ value: 'online', label: '在线' }, { value: 'offline', label: '离线' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
