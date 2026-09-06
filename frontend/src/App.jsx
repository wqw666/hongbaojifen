import React, { useEffect, useState } from 'react'
import { Layout, Menu, Dropdown, Avatar, message, Modal, Form, Input } from 'antd'
import {
  UserOutlined, HistoryOutlined, TeamOutlined, CrownOutlined,
  BookOutlined, ThunderboltOutlined, GiftOutlined, PlayCircleOutlined,
  LogoutOutlined, SafetyOutlined, KeyOutlined
} from '@ant-design/icons'
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom'
import api from './api'
import Login from './components/Login'
import MemberManager from './components/MemberManager'
import OperationLogs from './components/OperationLogs'
import QqAccountManager from './components/QqAccountManager'
import QqGroupManager from './components/QqGroupManager'
import DictManager from './components/DictManager'
import ExecutorManager from './components/ExecutorManager'
import PlayRuleManager from './components/PlayRuleManager'
import GameRecords from './components/GameRecords'
import AdminUsers from './components/AdminUsers'

const { Sider, Header, Content } = Layout

const MENU = [
  { key: '/members', icon: <UserOutlined />, label: '会员管理' },
  { key: '/qq-groups', icon: <TeamOutlined />, label: 'QQ群管理' },
  { key: '/operators', icon: <CrownOutlined />, label: '操作员管理' },
  { key: '/dicts', icon: <BookOutlined />, label: '配置管理' },
  { key: '/executors', icon: <ThunderboltOutlined />, label: '执行器管理' },
  { key: '/rules', icon: <GiftOutlined />, label: '会员玩法管理' },
  { key: '/game-records', icon: <PlayCircleOutlined />, label: '游戏记录' },
  { key: '/logs', icon: <HistoryOutlined />, label: '操作记录' },
]

/** 用户管理菜单 — 仅内置超级管理员 admin 可见（后端接口同样限制） */
const ADMIN_MENU = [{ key: '/admin-users', icon: <SafetyOutlined />, label: '用户管理' }]

function App() {
  const [token, setToken] = useState(localStorage.getItem('at'))
  const [username, setUsername] = useState('')
  const [pwdOpen, setPwdOpen] = useState(false)
  const [pwdForm] = Form.useForm()
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    if (!token) return
    api.get('/api/auth/verify').then(res => {
      setUsername(res.data?.data?.username || 'admin')
    }).catch(() => {
      localStorage.removeItem('at')
      setToken(null)
    })
  }, [token])

  const logout = () => {
    localStorage.removeItem('at')
    setToken(null)
    navigate('/')
    message.success('已退出登录')
  }

  /** 任意登录用户修改自己的密码（原密码校验由后端执行；当前会话不失效） */
  const submitPwd = async values => {
    try {
      await api.put('/api/auth/password', {
        old_password: values.old_password,
        new_password: values.new_password,
      })
      message.success('密码已更新，下次登录请使用新密码')
      setPwdOpen(false)
      pwdForm.resetFields()
    } catch (e) {
      message.error(e.response?.data?.message || '修改失败')
    }
  }

  const userMenuItems = [
    { key: 'pwd', icon: <KeyOutlined />, label: '修改密码', onClick: () => setPwdOpen(true) },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout },
  ]

  if (!token) return <Login onSuccess={() => setToken(localStorage.getItem('at'))} />

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="dark" style={{ background: '#1f1f1f' }}>
        <div style={{ height: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 16, fontWeight: 600 }}>
          🧧 红包积分管理
        </div>
        <Menu
          theme="dark"
          mode="inline"
          style={{ background: '#1f1f1f' }}
          selectedKeys={[location.pathname]}
          onClick={({ key }) => navigate(key)}
          items={[...MENU, ...(username === 'admin' ? ADMIN_MENU : [])]}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', boxShadow: '0 1px 4px rgba(0,0,0,0.08)' }}>
          <Dropdown menu={{ items: userMenuItems }}>
            <div style={{ cursor: 'pointer' }}>
              <Avatar style={{ background: '#ff4d4f', marginRight: 8 }} icon={<UserOutlined />} />
              <span>{username || 'admin'}</span>
            </div>
          </Dropdown>
        </Header>
        <Content style={{ margin: 16 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/members" replace />} />
            <Route path="/members" element={<MemberManager />} />
            <Route path="/logs" element={<OperationLogs />} />
            <Route path="/operators" element={<QqAccountManager />} />
            <Route path="/qq-groups" element={<QqGroupManager />} />
            <Route path="/dicts" element={<DictManager />} />
            <Route path="/executors" element={<ExecutorManager />} />
            <Route path="/rules" element={<PlayRuleManager />} />
            <Route path="/game-records" element={<GameRecords />} />
            <Route path="/admin-users" element={<AdminUsers />} />
            <Route path="*" element={<Navigate to="/members" replace />} />
          </Routes>
        </Content>
      </Layout>

      {/* 修改密码（所有登录用户） */}
      <Modal title="修改密码" open={pwdOpen} onCancel={() => { setPwdOpen(false); pwdForm.resetFields() }}
             onOk={() => pwdForm.submit()} destroyOnClose>
        <Form form={pwdForm} onFinish={submitPwd} layout="vertical">
          <Form.Item name="old_password" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
            <Input.Password placeholder="当前登录密码" />
          </Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '密码至少 6 位' },
          ]}>
            <Input.Password placeholder="至少 6 位" />
          </Form.Item>
          <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator: (_, value) =>
                !value || getFieldValue('new_password') === value
                  ? Promise.resolve()
                  : Promise.reject(new Error('两次输入的密码不一致')),
            }),
          ]}>
            <Input.Password placeholder="再次输入新密码" />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}

export default App
