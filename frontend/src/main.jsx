import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#ff4d4f', colorInfo: '#ff4d4f', colorLink: '#ff4d4f' } }}>
      <BrowserRouter basename="/admin">
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>
)
