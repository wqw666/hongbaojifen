import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Card, Table, Input, Button, Space, Modal, Form, Upload, message, Popconfirm, Tag, Alert, Typography } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
         DownloadOutlined, InboxOutlined } from '@ant-design/icons'
import api from '../api'

const { Text } = Typography
const { Dragger } = Upload

export default function PlayRuleManager() {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [agentUrl, setAgentUrl] = useState('')
  const [agentKey, setAgentKey] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/api/admin/rules', { params: { keyword } })
      setList(res.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [keyword])

  useEffect(() => { load() }, [load])

  // 显示 agent 拉取用的开放接口说明
  useEffect(() => {
    const base = window.location.origin
    setAgentUrl(base.replace(':3002', ':8892'))
  }, [])

  const submitUpload = async values => {
    if (!file) { message.warning('请选择文件'); return }
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('name', values.name)
      fd.append('description', values.description || '')
      fd.append('version', values.version || '1.0')
      await api.post('/api/admin/rules', fd)
      message.success('上传成功')
      setUploadOpen(false)
      load()
    } finally {
      setUploading(false)
    }
  }

  const submitEdit = async values => {
    await api.put(`/api/admin/rules/${editRow.id}`, values)
    message.success('已保存')
    setEditOpen(false)
    load()
  }

  const remove = async row => {
    await api.delete(`/api/admin/rules/${row.id}`)
    message.success('已删除')
    load()
  }

  const download = async row => {
    // 直接打开下载链接（带 token）
    const token = localStorage.getItem('at')
    const a = document.createElement('a')
    a.href = `/api/admin/rules/${row.id}/download`
    a.download = row.name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  const columns = [
    { title: '玩法名称', dataIndex: 'name', width: 160 },
    { title: '说明', dataIndex: 'description', ellipsis: true },
    { title: '大小', dataIndex: 'file_size', width: 100, render: v =>
        v >= 1024 * 1024 ? `${(v / 1024 / 1024).toFixed(2)}MB` : `${(v / 1024).toFixed(1)}KB` },
    { title: '版本', dataIndex: 'version', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: v =>
        <Tag color={v === 'active' ? 'green' : 'default'}>{v === 'active' ? '启用' : '停用'}</Tag> },
    { title: '更新时间', dataIndex: 'updated_at', width: 170 },
    {
      title: '操作', width: 200,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<DownloadOutlined />} onClick={() => download(row)}>下载</Button>
          <Button size="small" icon={<EditOutlined />}
                  onClick={() => { setEditRow(row); editForm.setFieldsValue(row); setEditOpen(true) }}>编辑</Button>
          <Popconfirm title="确认删除？（磁盘文件一并删除）" onConfirm={() => remove(row)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="会员玩法管理">
      <Alert style={{ marginBottom: 16 }} type="info" showIcon
             message="执行器（agent）开放接口拉取玩法"
             description={`列表: ${agentUrl}/api/open/rules　下载: ${agentUrl}/api/open/rules/{id}/download　（需请求头 X-Api-Key: 系统配置的密钥，仅"启用"状态的玩法可被拉取）`} />

      <Space style={{ marginBottom: 16 }}>
        <Input placeholder="玩法名称搜索" allowClear style={{ width: 200 }} value={keyword}
               onChange={e => setKeyword(e.target.value)} onPressEnter={load} />
        <Button type="primary" icon={<SearchOutlined />} onClick={load}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={() => { setKeyword(''); load() }}>重置</Button>
        <Button type="primary" icon={<PlusOutlined />}
                onClick={() => { uploadForm.resetFields(); setFile(null); setUploadOpen(true) }}>上传玩法文件</Button>
      </Space>

      <Table rowKey="id" size="middle" columns={columns} dataSource={list} loading={loading}
             pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }} />

      {/* 上传 */}
      <Modal title="上传玩法文件" open={uploadOpen} onCancel={() => setUploadOpen(false)}
             onOk={() => uploadForm.submit()} confirmLoading={uploading} destroyOnClose>
        <Form form={uploadForm} onFinish={submitUpload} layout="vertical">
          <Form.Item label="玩法文件" required style={{ marginBottom: 12 }}>
            <Dragger beforeUpload={f => { setFile(f); return false }} maxCount={1} fileList={file ? [file] : []}
                     onRemove={() => setFile(null)}>
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽文件到此处</p>
              <p className="ant-upload-hint">游戏规则文本 / 脚本代码，文件不超过 20MB</p>
            </Dragger>
          </Form.Item>
          <Form.Item name="name" label="玩法名称" rules={[{ required: true, message: '请输入玩法名称' }]}>
            <Input placeholder="如：猜红包大小、答题闯关" />
          </Form.Item>
          <Form.Item name="description" label="玩法说明"><Input placeholder="给执行器看的玩法简介" /></Form.Item>
          <Form.Item name="version" label="版本" initialValue="1.0"><Input placeholder="如：1.0" /></Form.Item>
        </Form>
      </Modal>

      {/* 编辑 */}
      <Modal title="编辑玩法" open={editOpen} onCancel={() => setEditOpen(false)}
             onOk={() => editForm.submit()} destroyOnClose>
        <Form form={editForm} onFinish={submitEdit} layout="vertical">
          <Form.Item name="name" label="玩法名称" rules={[{ required: true, message: '请输入玩法名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="玩法说明"><Input /></Form.Item>
          <Form.Item name="version" label="版本"><Input /></Form.Item>
          <Form.Item name="status" label="状态（启用 = 执行器可拉取）" initialValue="active">
            <Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
