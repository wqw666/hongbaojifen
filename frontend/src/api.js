import axios from 'axios'

const api = axios.create({ baseURL: '' })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('at')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  res => res,
  err => {
    // 登录接口 401 是「密码错误」，由 Login 页提示，不视为会话失效
    const isLoginReq = err.config?.url?.includes('/api/auth/login')
    if (err.response?.status === 401 && !isLoginReq) {
      localStorage.removeItem('at')
      window.location.reload()
    }
    return Promise.reject(err)
  }
)

export default api
