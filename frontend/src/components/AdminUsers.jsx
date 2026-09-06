import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Button, Space, Modal, Form, message, Popconfirm, Tag, Typography, Tooltip } from 'antd'
import { PlusOutlined, KeyOutlined, DeleteOutlined, SafetyOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

const { Text } = Typography

/**
 * 后台用户管理（仅超级管理员 admin 登录时菜单可见；后端接口同样只放行 admin）
 * 新增用户 / 重置密码 / 删除；内置 admin 账号不可删除。
 * 普通后台账号无此页面，改自己的密码走右上角头像下拉「修改密码」。
 */
export default function AdminUsers() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [pwdOpen, setPwdOpen] = useState(false)
  const [pwdRow, setPwdRow] = useState(null)
  const [addForm] = Form.useForm()
  const [pwdForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/users')
      setList(res.data?.data || [])
    } catch (e) {
      message.error(e.response?.data?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const submitAdd = async values => {
    try {
      await api.post('/api/admin/users', values)
      message.success(`已新增用户 ${values.username}`)
      setAddOpen(false)
      load()
    } catch (e) {
      message.error(e.response?.data?.message || '新增失败')
    }
  }

  const submitPwd = async values => {
    try {
      await api.put(`/api/admin/users/${pwdRow.id}/password`, { password: values.password })
      message.success(`已重置 ${pwdRow.username} 的密码`)
      setPwdOpen(false)
    } catch (e) {
      message.error(e.response?.data?.message || '重置失败')
    }
  }

  const remove = async row => {
    try {
      await api.delete(`/api/admin/users/${row.id}`)
      message.success(`已删除用户 ${row.username}`)
      load()
    } catch (e) {
      message.error(e.response?.data?.message || '删除失败')
    }
  }

  const columns = [
    {
      title: '用户名', dataIndex: 'username', width: 200,
      render: (v, row) => row.builtin
        ? <Space><span>{v}</span><Tag color="gold" icon={<SafetyOutlined />}>超级管理员</Tag></Space>
        : v,
    },
    { title: '昵称', dataIndex: 'nickname', ellipsis: true },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: v => v || '—' },
    {
      title: '操作', width: 170, fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<KeyOutlined />}
                  onClick={() => { setPwdRow(row); pwdForm.resetFields(); setPwdOpen(true) }}>
            重置密码
          </Button>
          <Tooltip title={row.builtin ? '内置超级管理员不可删除' : ''}>
            <Popconfirm title={`确认删除用户 ${row.username}？删除后该账号无法登录`}
                        onConfirm={() => remove(row)} disabled={row.builtin}>
              <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={row.builtin} />
            </Popconfirm>
          </Tooltip>
        </Space>
      ),
    },
  ]

  return (
    <Card title="后台用户管理">
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { addForm.resetFields(); setAddOpen(true) }}>新增用户</Button>
        <Text type="secondary" style={{ fontSize: 12 }}>
          本页仅超级管理员可见；内置 admin 不可删除，其余账号可登录总后台做日常管理
        </Text>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      {/* 新增用户 */}
      <Modal title="新增用户" open={addOpen} onCancel={() => setAddOpen(false)}
             onOk={() => addForm.submit()} destroyOnClose>
        <Form form={addForm} onFinish={submitAdd} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[
            { required: true, message: '请输入用户名' },
            { pattern: /^\S{2,32}$/, message: '2-32 个字符且不含空格' },
          ]}>
            <Input placeholder="登录总后台用" />
          </Form.Item>
          <Form.Item name="nickname" label="昵称">
            <Input placeholder="显示名称（留空则同用户名）" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[
            { required: true, message: '请输入密码' },
            { min: 6, message: '密码至少 6 位' },
          ]}>
            <Input.Password placeholder="至少 6 位" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码 */}
      <Modal title={`重置密码 — ${pwdRow?.username || ''}`} open={pwdOpen} onCancel={() => setPwdOpen(false)}
             onOk={() => pwdForm.submit()} destroyOnClose>
        <Form form={pwdForm} onFinish={submitPwd} layout="vertical">
          <Form.Item name="password" label="新密码" rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '密码至少 6 位' },
          ]}>
            <Input.Password placeholder="至少 6 位" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
