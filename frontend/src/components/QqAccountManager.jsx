import React, { useEffect, useState, useCallback } from 'react'
import { Card, Table, Input, Button, Space, Modal, Form, message, Popconfirm } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import api from '../api'

/**
 * QQ号管理（type=qq 普通号池 / type=admin_qq 管理员号）
 * 两个菜单复用本组件，通过 type 区分
 */
export default function QqAccountManager({ type = 'qq' }) {
  const isAdmin = type === 'admin_qq'
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [qq, setQq] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [form] = Form.useForm()
  const [batchForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/qq-accounts', { params: { type, qq } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [type, qq])

  useEffect(() => { load() }, [load])

  const submitAdd = async values => {
    await api.post('/api/admin/qq-accounts', { ...values, type })
    message.success('已新增')
    setAddOpen(false)
    load()
  }

  const submitBatch = async values => {
    const res = await api.post('/api/admin/qq-accounts/batch', { ...values, type })
    message.success(res.data?.message || '批量导入完成')
    setBatchOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/qq-accounts/${row.id}`)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: 'QQ号', dataIndex: 'qq', width: 130 },
    { title: '昵称', dataIndex: 'nickname', width: 150 },
    { title: '类型', dataIndex: 'type', width: 110, render: v =>
        <span>{v === 'admin_qq' ? '管理员号' : '普通号'}</span> },
    { title: '状态', dataIndex: 'status', width: 90 },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    { title: '添加时间', dataIndex: 'created_at', width: 170 },
    {
      title: '操作', width: 70,
      render: (_, row) => (
        <Popconfirm title="确认删除？" onConfirm={() => remove(row)}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  return (
    <Card title={isAdmin ? '管理员QQ号管理' : 'QQ号管理'}>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="QQ号搜索" allowClear style={{ width: 180 }} value={qq}
               onChange={e => setQq(e.target.value)} onPressEnter={load} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setQq(''); load() }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>新增QQ号</Button>
        <Button icon={<PlusOutlined />} onClick={() => setBatchOpen(true)}>批量导入</Button>
      </Space>

      <Table rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      {/* 新增单个 */}
      <Modal title="新增QQ号" open={addOpen} onCancel={() => setAddOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submitAdd} layout="vertical">
          <Form.Item name="qq" label="QQ号" rules={[{ required: true, message: '请输入QQ号' }]}>
            <Input placeholder="5-12位数字" />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input placeholder="备注信息" /></Form.Item>
        </Form>
      </Modal>

      {/* 批量导入 */}
      <Modal title="批量导入QQ号" open={batchOpen} onCancel={() => setBatchOpen(false)}
             onOk={() => batchForm.submit()} destroyOnClose>
        <Form form={batchForm} onFinish={submitBatch} layout="vertical">
          <Form.Item name="qq_text" label="QQ号列表" rules={[{ required: true, message: '请输入QQ号' }]}>
            <Input.TextArea rows={6} placeholder={'每行一个QQ号，也支持逗号/空格分隔\n格式不正确的行自动跳过，已存在的自动跳过'} />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input placeholder="统一备注" /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
