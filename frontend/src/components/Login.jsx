import React, { useState } from 'react'
import { Card, Form, Input, Button, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import api from '../api'

export default function Login({ onSuccess }) {
  const [loading, setLoading] = useState(false)

  const onFinish = async values => {
    setLoading(true)
    try {
      const res = await api.post('/api/auth/login', values)
      localStorage.setItem('at', res.data?.data?.token)
      message.success('登录成功')
      onSuccess && onSuccess()
    } catch (e) {
      message.error(e?.response?.data?.message || '登录失败，请检查用户名密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #1f1f1f 0%, #3a0d0d 100%)' }}>
      <Card style={{ width: 380, boxShadow: '0 4px 16px rgba(0,0,0,0.3)' }}>
        <div style={{ textAlign: 'center', fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
          🧧 红包积分管理系统
        </div>
        <div style={{ textAlign: 'center', color: '#999', marginBottom: 24 }}>管理员登录</div>
        <Form onFinish={onFinish} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>登 录</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
