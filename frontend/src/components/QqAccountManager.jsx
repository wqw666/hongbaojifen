import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Button, Space, Modal, Form, Select, message, Popconfirm, Tag, Typography, Tooltip } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

const { Text } = Typography

/**
 * 操作员管理（管理员QQ）
 * 操作员即各执行器机器上登录 QQ 的管理员；agent 启动后心跳自动注册，
 * 总后台在此标注 状态(正常/停用) 与 手动上下分权限，并查看最近登录(在线)时间/IP/主机
 */
export default function QqAccountManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [qq, setQq] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/qq-accounts', { params: { qq } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [qq])

  useEffect(() => { load() }, [load])

  const submitAdd = async values => {
    await api.post('/api/admin/qq-accounts', values)
    message.success('已新增，该QQ登录 agent 后自动完成绑定')
    setAddOpen(false)
    load()
  }

  const submitEdit = async values => {
    await api.put(`/api/admin/qq-accounts/${editRow.id}`, values)
    message.success('已更新')
    setEditOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/qq-accounts/${row.id}`)
    message.success('已删除')
    load()
  }

  const columns = [
    { title: 'QQ号', dataIndex: 'qq', width: 120 },
    { title: '昵称', dataIndex: 'nickname', ellipsis: true },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={v === 'active' ? 'green' : 'orange'}>{v === 'active' ? '正常' : '停用'}</Tag>,
    },
    {
      title: '手动上下分', dataIndex: 'can_manual_points', width: 110,
      render: (v, row) => v === 'allowed'
        ? <Tag color="green">允许</Tag>
        : <Tooltip title="该操作员不能手动上/下分；游戏自动结算不受限制"><Tag color="red">禁止</Tag></Tooltip>,
    },
    { title: '最近登录时间', dataIndex: 'last_login_at', width: 170, render: v => v || '—' },
    { title: '最近登录IP', dataIndex: 'last_login_ip', width: 140, render: v => v || '—' },
    { title: '登录主机', dataIndex: 'last_host', width: 140, ellipsis: true, render: v => v || '—' },
    { title: '备注', dataIndex: 'remark', ellipsis: true },
    { title: '添加时间', dataIndex: 'created_at', width: 170 },
    {
      title: '操作', width: 130, fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => { setEditRow(row); editForm.setFieldsValue(row); setEditOpen(true) }}>编辑</Button>
          <Popconfirm title={`确认删除操作员 ${row.qq}？`} onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="操作员管理">
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="QQ号搜索" allowClear style={{ width: 180 }} value={qq}
               onChange={e => setQq(e.target.value)} onPressEnter={load} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setQq(''); load() }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>新增操作员</Button>
        <Text type="secondary" style={{ fontSize: 12 }}>
          执行器 agent 启动登录管理员QQ后会自动注册到这里，无需手工新增
        </Text>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      {/* 新增操作员 */}
      <Modal title="新增操作员" open={addOpen} onCancel={() => setAddOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submitAdd} layout="vertical">
          <Form.Item name="qq" label="管理员QQ号" rules={[{ required: true, message: '请输入QQ号' }]}>
            <Input placeholder="5-12位数字（agent 将用此QQ登录）" />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input placeholder="备注信息" /></Form.Item>
        </Form>
      </Modal>

      {/* 编辑操作员：状态 + 手动上下分权限 */}
      <Modal title={`编辑操作员 — ${editRow?.qq || ''}`} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => editForm.submit()} destroyOnClose>
        <Form form={editForm} onFinish={submitEdit} layout="vertical" initialValues={editRow || {}}>
          <Form.Item name="nickname" label="昵称"><Input placeholder="QQ昵称" /></Form.Item>
          <Form.Item name="status" label="状态" rules={[{ required: true }]}>
            <Select options={[{ value: 'active', label: '正常' }, { value: 'disabled', label: '停用' }]} />
          </Form.Item>
          <Form.Item name="can_manual_points" label="手动上/下分权限" rules={[{ required: true }]}
                     extra="禁止 = 操作员不能通过手动调分干预积分；游戏自动结算不受此限制">
            <Select options={[{ value: 'allowed', label: '允许' }, { value: 'denied', label: '禁止' }]} />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input placeholder="备注信息" /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
