import React, { useEffect, useState } from 'react'
import { Layout, Menu, Dropdown, Avatar, message, Modal, Form, Input, Button } from 'antd'
import {
  UserOutlined, HistoryOutlined, TeamOutlined, CrownOutlined,
  BookOutlined, ThunderboltOutlined, GiftOutlined, PlayCircleOutlined,
  LogoutOutlined, SafetyOutlined, KeyOutlined, BarChartOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom'
import api from './api'
import Login from './components/Login'
import Report from './components/Report'
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

/**
 * 报表（增值服务）开关：当前隐藏菜单、不上客户页面（代码/路由保留，随时可开）。
 * 后续按增值服务收费后，把这里改为 true 并重新构建前端即可放出菜单；
 * 已登录用户也可通过 URL /report 直达（不删路由，只藏入口）。
 */
const REPORT_MENU_ENABLED = false

const MENU = [
  { key: '/members', icon: <UserOutlined />, label: '会员管理' },
  ...(REPORT_MENU_ENABLED
    ? [{ key: '/report', icon: <BarChartOutlined />, label: '报表' }]
    : []),
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
  const [disOpen, setDisOpen] = useState(false)
  const [disText, setDisText] = useState('')
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
    // 登录免责声明：字典 key=disclaimer_text（配置管理可编辑；值非空才弹，确认后关闭）
    api.get('/api/admin/dicts', { params: { key: 'disclaimer_text' } })
      .then(res => {
        const row = (res.data?.data || []).find(r => r.key === 'disclaimer_text')
        if (row?.value) { setDisText(row.value); setDisOpen(true) }
      })
      .catch(() => { /* 读取失败不弹，不挡使用 */ })
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
    <>
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
            <Route path="/report" element={<Report />} />
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

    {/* 免责声明：登录后弹窗（只读字典文本；唯一确认按钮关闭，不可跳过） */}
    <Modal
      title={<span><WarningOutlined style={{ color: '#cf1322' }} /> 免责声明</span>}
      open={disOpen}
      closable={false}
      maskClosable={false}
      keyboard={false}
      width={680}
      footer={<Button type="primary" onClick={() => setDisOpen(false)}>我已阅读并确认</Button>}
    >
      <div style={{ whiteSpace: 'pre-wrap', maxHeight: '55vh', overflow: 'auto', lineHeight: 1.9 }}>
        {disText}
      </div>
    </Modal>
    </Layout>
    </>
  )
}

export default App
