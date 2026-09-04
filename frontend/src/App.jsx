import React, { useEffect, useState } from 'react'
import { Layout, Menu, Dropdown, Avatar, message } from 'antd'
import {
  UserOutlined, HistoryOutlined, TeamOutlined, CrownOutlined,
  BookOutlined, ThunderboltOutlined, GiftOutlined, PlayCircleOutlined, LogoutOutlined
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

function App() {
  const [token, setToken] = useState(localStorage.getItem('at'))
  const [username, setUsername] = useState('')
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
          items={MENU}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', boxShadow: '0 1px 4px rgba(0,0,0,0.08)' }}>
          <Dropdown menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout }] }}>
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
            <Route path="*" element={<Navigate to="/members" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default App
