import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Select, Button, Space, Modal, Form, message, Popconfirm, Tag,
         Typography, Alert, Tooltip, Radio } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
         KeyOutlined, StopOutlined, CheckCircleOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

const { Text } = Typography

export default function ExecutorManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState()
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [newToken, setNewToken] = useState('')
  // 封禁弹窗：仅封禁 / 封禁并重置token（双按钮由弹窗单选承载）
  const [banRow, setBanRow] = useState(null)
  const [banMode, setBanMode] = useState('plain')
  const [banForm] = Form.useForm()
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

  const submitBan = async values => {
    const res = await api.post(`/api/admin/executors/${banRow.id}/ban`, {
      reason: values.reason,
      reset_token: banMode === 'reset',
    })
    setBanRow(null)
    if (res.data?.token) setNewToken(res.data.token)
    message.success(res.data?.message || '已封禁')
    load()
  }

  const unban = async row => {
    await api.post(`/api/admin/executors/${row.id}/unban`)
    message.success('已解封（若封禁时重置过 token，请把新 token 配给该执行器）')
    load()
  }

  const openBan = row => {
    setBanRow(row)
    setBanMode('plain')
    banForm.resetFields()
  }

  const statusTag = row => {
    if (row.banned_at) {
      return <Tooltip title={row.ban_reason ? `封禁原因：${row.ban_reason}` : `封禁时间：${row.banned_at}`}>
        <Tag color="red">已封禁</Tag>
      </Tooltip>
    }
    return <Tag color={row.status === 'online' ? 'green' : 'default'}>{row.status === 'online' ? '在线' : '离线'}</Tag>
  }

  const columns = [
    { title: '名称', dataIndex: 'name', width: 130 },
    { title: '状态', dataIndex: 'status', width: 90, render: (_, row) => statusTag(row) },
    { title: '版本', dataIndex: 'version', width: 90, render: v => v || '—' },
    {
      title: '管理员QQ', dataIndex: 'admin_qq', width: 120,
      render: (v, row) => v
        ? <Tooltip title={row.admin_nickname ? `昵称：${row.admin_nickname}` : ''}><Text>{v}</Text></Tooltip>
        : <Text type="secondary">未上报</Text>,
    },
    { title: '主机', dataIndex: 'host', width: 140, ellipsis: true, render: v => v || '—' },
    { title: '最后心跳IP', dataIndex: 'last_ip', width: 130, render: v => v || '—' },
    { title: '最后心跳', dataIndex: 'last_heartbeat', width: 165, render: v => v || '—' },
    {
      title: '管理群', dataIndex: 'groups', width: 100, render: (v) => {
        if (!v || v.length === 0) return <Text type="secondary">—</Text>
        return <Tooltip title={v.map(g => `${g.group_name}(${g.group_id})`).join('\n')}>
          <span style={{ cursor: 'help' }}>{v.length} 个群</span>
        </Tooltip>
      },
    },
    {
      title: '操作', width: 260, fixed: 'right',
      render: (_, row) => row.banned_at ? (
        <Space size={4}>
          <Button size="small" type="primary" ghost icon={<CheckCircleOutlined />} onClick={() => unban(row)}>解封</Button>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => { setEditRow(row); form.setFieldsValue(row); setEditOpen(true) }}>编辑</Button>
          <Popconfirm title="确认删除？" onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ) : (
        <Space size={4}>
          <Button size="small" danger icon={<StopOutlined />} onClick={() => openBan(row)}>封禁</Button>
          <Button size="small" icon={<KeyOutlined />} onClick={() => resetToken(row)}>重置Token</Button>
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
    <Card title="执行器管理">
      {newToken && (
        <Alert style={{ marginBottom: 16 }} type="warning" showIcon closable onClose={() => setNewToken('')}
               message="执行器 Token（仅显示一次，请立即复制保存到 agent 配置）"
               description={<Text copyable style={{ fontSize: 14 }}>{newToken}</Text>} />
      )}
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="名称/主机/管理员QQ搜索" allowClear style={{ width: 200 }} value={keyword}
               onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
        <Select placeholder="状态" allowClear style={{ width: 120 }} value={status} onChange={setStatus}
                options={[{ value: 'online', label: '在线' }, { value: 'offline', label: '离线' },
                          { value: 'banned', label: '封禁' }]} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); setStatus(undefined) }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { setEditRow(null); form.resetFields(); setEditOpen(true) }}>新增执行器</Button>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      <Modal title={editRow ? '编辑执行器' : '新增执行器'} open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => form.submit()} destroyOnClose>
        <Form form={form} onFinish={submit} layout="vertical">
          <Form.Item name="name" label="执行器名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：机房A-1号执行器" />
          </Form.Item>
          <Form.Item name="version" label="程序版本"><Input placeholder="如：1.0.0" /></Form.Item>
          <Form.Item name="host" label="主机"><Input placeholder="机器名/部署位置，agent 心跳会持续更新" /></Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            在线状态与管理员QQ由 agent 心跳自动上报，无需手工设置；一个执行器可管理多个群
          </Text>
        </Form>
      </Modal>

      <Modal title={`封禁执行器 — ${banRow?.name || ''}`} open={!!banRow} onCancel={() => setBanRow(null)}
             onOk={() => banForm.submit()} destroyOnClose>
        <Form form={banForm} onFinish={submitBan} layout="vertical">
          <Form.Item label="封禁方式" required style={{ marginBottom: 8 }}>
            <Radio.Group value={banMode} onChange={e => setBanMode(e.target.value)}>
              <Radio value="plain">仅封禁（解封后原 Token 恢复）</Radio>
              <br />
              <Radio value="reset">封禁并重置 Token（旧 Token 立即作废，防攻破续联）</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="reason" label="封禁原因" rules={[{ required: true, message: '请输入封禁原因' }]}>
            <Input.TextArea rows={3} placeholder="如：执行器疑似被攻破、上报异常数据" />
          </Form.Item>
          <Text type="warning" style={{ fontSize: 12 }}>
            封禁后该执行器所有心跳/上报/命令请求立即被拒，游戏同步停摆
          </Text>
        </Form>
      </Modal>
    </Card>
  )
}
