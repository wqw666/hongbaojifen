import React, { useEffect, useState, useCallback } from 'react'
import { Card, Input, Button, Space, Tag, Typography, Drawer, Timeline, Alert, Tooltip } from 'antd'
import { SearchOutlined, ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons'
import api from '../api'
import ResizableTable from './ResizableTable'

const { Text } = Typography

/**
 * 游戏记录：agent 上报的对局列表 + 回放（事件时间线）
 * 列表 GET /api/admin/game-records?group_id=&play_name=&page=&size=
 * 详情 GET /api/admin/game-records/{id} → {record, events}
 */
export default function GameRecords() {
  const [list, setList] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [groupId, setGroupId] = useState('')
  const [playName, setPlayName] = useState('')

  // 回放抽屉
  const [replayOpen, setReplayOpen] = useState(false)
  const [replay, setReplay] = useState(null)
  const [replayLoading, setReplayLoading] = useState(false)

  const load = useCallback(async p => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/game-records', {
        params: { group_id: groupId, play_name: playName, page: p || page, size: 20 },
      })
      setList(res.data?.data?.list || [])
      setTotal(res.data?.data?.total || 0)
      setPage(res.data?.data?.page || p || page)
    } finally {
      setLoading(false)
    }
  }, [groupId, playName, page])

  useEffect(() => { load(1) }, [load]) // eslint-disable-line react-hooks/exhaustive-deps

  const openReplay = async row => {
    setReplayOpen(true)
    setReplayLoading(true)
    setReplay(null)
    try {
      const res = await api.get(`/api/admin/game-records/${row.id}`)
      setReplay(res.data?.data || null)
    } finally {
      setReplayLoading(false)
    }
  }

  const deltaColor = v => v > 0 ? '#52c41a' : v < 0 ? '#ff4d4f' : '#999'
  const deltaText = v => v > 0 ? `+${v}` : `${v}`
  // R8：净变动列改为「抽水」——每局玩家净变动合计 = -抽水，展示取正值绿色（如 -40 → +40）
  const rakeText = v => `+${Math.abs(v || 0)}`

  const columns = [
    { title: '局号', dataIndex: 'round_id', width: 190, ellipsis: true },
    { title: '玩法', dataIndex: 'play_name', width: 100, render: v => v || '—' },
    { title: '群号', dataIndex: 'group_id', width: 100, render: v => v || '—' },
    { title: '执行器', dataIndex: 'executor_name', width: 120, render: v => v || '—' },
    { title: '操作员QQ', dataIndex: 'operator_qq', width: 110, render: v => v || '—' },
    { title: '结算人数', dataIndex: 'member_count', width: 90 },
    {
      title: '抽水', dataIndex: 'total_delta', width: 90,
      render: v => <Text strong style={{ color: '#52c41a' }}>{rakeText(v)}</Text>,
    },
    { title: '事件数', dataIndex: 'event_count', width: 80 },
    {
      title: '异常', dataIndex: 'warning_count', width: 90,
      render: (v, row) => (v > 0 || row.warning)
        ? <Tooltip title={row.warning || ''}><Tag color="red">异常({v || (row.warning || '').split('; ').filter(Boolean).length})</Tag></Tooltip>
        : <Tag color="green">无</Tag>,
    },
    { title: '上报时间', dataIndex: 'created_at', width: 165 },
    {
      title: '操作', width: 80, fixed: 'right',
      render: (_, row) => (
        <Button size="small" icon={<PlayCircleOutlined />} onClick={() => openReplay(row)}>回放</Button>
      ),
    },
  ]

  const rec = replay?.record
  const eventRows = replay?.events || []

  return (
    <Card title="游戏记录">
      <Space style={{ marginBottom: 16 }} wrap>
        <Input placeholder="群号" allowClear style={{ width: 150 }} value={groupId}
               onChange={e => setGroupId(e.target.value)} onPressEnter={() => load(1)} />
        <Input placeholder="玩法名" allowClear style={{ width: 150 }} value={playName}
               onChange={e => setPlayName(e.target.value)} onPressEnter={() => load(1)} />
        <Button type="primary" icon={<SearchOutlined />} onClick={() => load(1)}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setGroupId(''); setPlayName(''); load(1) }}>重置</Button>
        <Text type="secondary" style={{ fontSize: 12 }}>agent 每局游戏结算后自动上报，默认保留 30 天（可在「字典」调整 game_record_retention_days）</Text>
      </Space>

      <ResizableTable rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{
               current: page, total, pageSize: 20,
               showTotal: t => `共 ${t} 条`,
               onChange: p => load(p),
             }} />

      <Drawer title="对局回放" width={988} open={replayOpen} onClose={() => setReplayOpen(false)} loading={replayLoading}>
        {rec && (
          <>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Space wrap>
                <Text strong>{rec.play_name || '未知玩法'}</Text>
                <Text type="secondary">局号 {rec.round_id}</Text>
                <Tag>群 {rec.group_id || '—'}</Tag>
                <Tag color="volcano">{rec.executor_name}（操作员 {rec.operator_qq || '—'}）</Tag>
                <Tag color="green">抽水 {rakeText(rec.total_delta)}</Tag>
                <Tag>结算 {rec.member_count} 人 / {rec.event_count} 事件</Tag>
                <Text type="secondary">{rec.created_at}</Text>
              </Space>
              {rec.warning && (
                <Alert style={{ marginTop: 12 }} type="warning" showIcon
                       message={`本局异常 ${rec.warning_count || 0} 条（部分事件未入账）`}
                       description={<div style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>
                         {rec.warning_detail || rec.warning}
                       </div>} />
              )}
            </Card>
            {eventRows.length === 0 && <Text type="secondary">无事件明细</Text>}
            {eventRows.length > 0 && (() => {
              const shown = eventRows.length > 300 ? eventRows.slice(-300) : eventRows
              return (
                <Timeline
                  items={shown.map((e) => ({
                    color: e.delta > 0 ? 'green' : e.delta < 0 ? 'red' : 'gray',
                    children: (
                      <div key={e.id}>
                        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                          <Text strong>{e.nickname || '—'}</Text>
                          <Text type="secondary">{e.qq}</Text>
                          {e.delta !== 0 &&
                            <Text strong style={{ color: deltaColor(e.delta) }}>{deltaText(e.delta)}</Text>}
                        </Space>
                        {e.msg && <div style={{ color: '#555', marginTop: 2 }}>{e.msg}</div>}
                        {e.reply && <div style={{ color: '#8a5a00', marginTop: 2 }}>→ {e.reply}</div>}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {e.ev_time || e.created_at}
                        </Text>
                      </div>
                    ),
                  }))}
                />
              )
            })()}
            {eventRows.length > 300 && (
              <div style={{ textAlign: 'center', marginTop: 8 }}>
                <Text type="secondary">共 {eventRows.length} 条消息记录（仅展示最新 300 条）</Text>
              </div>
            )}
          </>
        )}
        {!replay && !replayLoading && <Text type="secondary">加载失败</Text>}
      </Drawer>
    </Card>
  )
}
