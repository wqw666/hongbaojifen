import React, { useEffect, useState, useCallback } from 'react'
import { Card, Table, Input, Button, Space, Tag } from 'antd'
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons'
import api from '../api'

const ACTION_COLOR = {
  登录: 'blue', 上分: 'green', 下分: 'red', 新增会员: 'cyan', 更新会员: 'geekblue',
  删除会员: 'red', 新增QQ号: 'purple', 批量新增QQ号: 'purple', 删除QQ号: 'red',
  新增QQ群: 'orange', 更新QQ群: 'gold', 删除QQ群: 'red',
  新增执行器: 'volcano', 更新执行器: 'volcano', 删除执行器: 'red', 重置执行器token: 'volcano',
  上传玩法: 'magenta', 更新玩法: 'magenta', 删除玩法: 'red',
}

export default function OperationLogs() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/logs', { params: { keyword, limit: 300 } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [keyword])

  useEffect(() => { load() }, [load])

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 170 },
    { title: '操作人', dataIndex: 'operator', width: 110 },
    { title: '操作', dataIndex: 'action', width: 130, render: v => <Tag color={ACTION_COLOR[v] || 'default'}>{v}</Tag> },
    { title: '对象', dataIndex: 'target', width: 140, ellipsis: true },
    { title: '详情', dataIndex: 'detail', ellipsis: true },
    { title: 'IP', dataIndex: 'ip', width: 140 },
  ]

  return (
    <Card title="页面操作记录">
      <Space style={{ marginBottom: 16 }}>
        <Input placeholder="操作人/对象/详情 模糊搜索" allowClear style={{ width: 240 }} value={keyword}
               onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); load() }}>刷新</Button>
      </Space>
      <Table rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 50, showTotal: t => `共 ${t} 条` }} />
    </Card>
  )
}
