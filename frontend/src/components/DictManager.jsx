import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Button, Space, Modal, Form, message, Popconfirm } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

export default function DictManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [key, setKey] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/dicts', { params: { key } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [key])

  useEffect(() => { load() }, [load])

  const submit = async values => {
    if (editRow) {
      await api.put(`/api/admin/dicts/${editRow.id}`, values)
    } else {
      await api.post('/api/admin/dicts', values)
    }
    message.success('已保存')
    setEditOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/dicts/${row.id}`)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'Key', dataIndex: 'key', width: 180, render: v => <span style={{ fontWeight: 600 }}>{v}</span> },
    { title: '值', dataIndex: 'value', ellipsis: true },
    { title: '描述', dataIndex: 'description', width: 200, ellipsis: true },
    { title: '更新时间', dataIndex: 'updated_at', width: 170 },
    {
      title: '操作', width: 130,
      render: (_, row) => (
        <Space size={4}>
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
    <Card title="配置管理">
      <Space style={{ marginBottom: 16 }}>
        <Input placeholder="Key 模糊搜索" allowClear style={{ width: 200 }} value={key}
               onChange={e => setKey(e.target.value)} onPressEnter={load} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKey(''); load() }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { setEditRow(null); form.resetFields(); setEditOpen(true) }}>新增配置</Button>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      <Modal title={editRow ? '编辑配置' : '新增配置'} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submit} layout="vertical">
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入Key' }]}>
            <Input disabled={!!editRow} placeholder="如：game_prompt、redpacket_config" />
          </Form.Item>
          <Form.Item name="value" label="值" rules={[{ required: true, message: '请输入值' }]}>
            <Input.TextArea rows={4} placeholder="配置内容" />
          </Form.Item>
          <Form.Item name="description" label="描述"><Input placeholder="用途说明" /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
