import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Alert, Avatar, Button, Drawer, Layout, Space, Typography } from 'antd'
import {
  AppstoreOutlined,
  BarChartOutlined,
  CloudSyncOutlined,
  MenuOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { DashboardPage } from './pages/Dashboard'
import { ProjectDetailPage } from './pages/ProjectDetail'
import { SettingsPage } from './pages/SettingsPage'
import { OperationsPage } from './pages/Operations'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

interface NavigationItem {
  to: string
  label: string
  icon: React.ReactNode
  end?: boolean
}

const navigation: NavigationItem[] = [
  { to: '/', label: '机会池', icon: <AppstoreOutlined />, end: true },
  { to: '/operations', label: '处理状态', icon: <CloudSyncOutlined /> },
  { to: '/settings', label: '业务配置', icon: <SettingOutlined /> },
]

function Shell() {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileMenu, setMobileMenu] = useState(false)
  const location = useLocation()

  useEffect(() => {
    const update = () => setCollapsed(window.innerWidth < 1100)
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  const currentTitle = location.pathname.startsWith('/projects/') ? '项目详情' : navigation.find((item) => item.to === location.pathname)?.label ?? '机会池'

  const menu = (
    <nav className="sidebar-nav" aria-label="主导航">
      <div className="nav-group-title">工作台</div>
      {navigation.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setMobileMenu(false)}
          className={({ isActive }) => `nav-item${isActive ? ' nav-item-active' : ''}`}
        >
          <span className="nav-icon">{item.icon}</span>
          <span>{item.label}</span>
        </NavLink>
      ))}
      <div className="sidebar-note">
        <ThunderboltOutlined />
        <div>
          <Text strong>先看机会，再作判断</Text>
          <Text type="secondary">AI 结论仅基于已接入资料</Text>
        </div>
      </div>
    </nav>
  )

  return (
    <Layout className="app-layout">
      <Sider
        className="app-sider desktop-sider"
        theme="light"
        width={248}
        collapsedWidth={76}
        collapsible
        collapsed={collapsed}
        trigger={null}
      >
        <div className="brand-block">
          <div className="brand-mark"><BarChartOutlined /></div>
          {!collapsed && <div><Title level={4}>商机判断工作台</Title><Text type="secondary">销售筛选与方案</Text></div>}
        </div>
        {collapsed ? navigation.map((item) => <NavLink key={item.to} to={item.to} end={item.end} title={item.label} className={({ isActive }) => `collapsed-nav-item${isActive ? ' active' : ''}`}>{item.icon}</NavLink>) : menu}
        <Button className="sider-collapse" type="text" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'}>{collapsed ? '›' : '‹'}</Button>
      </Sider>
      <Drawer placement="left" open={mobileMenu} onClose={() => setMobileMenu(false)} closable={false} className="mobile-drawer" width={276}>
        <div className="brand-block mobile-brand"><div className="brand-mark"><BarChartOutlined /></div><div><Title level={4}>商机判断工作台</Title><Text type="secondary">销售筛选与方案</Text></div></div>
        {menu}
      </Drawer>
      <Layout>
        <Header className="app-header">
          <Space size={12}>
            <Button className="mobile-menu-button" type="text" icon={<MenuOutlined />} onClick={() => setMobileMenu(true)} aria-label="打开导航" />
            <div><Text type="secondary" className="breadcrumb-label">销售筛选平台</Text><Title level={3}>{currentTitle}</Title></div>
          </Space>
          <div className="header-account"><Avatar size={34} className="account-avatar">销</Avatar><div className="account-copy"><Text strong>销售团队</Text><Text type="secondary">内部工作台</Text></div></div>
        </Header>
        <Content className="app-content">
          <Alert className="scope-alert" type="info" showIcon message="这是内部判断工作台，AI 建议来自当前接入公告与业务资料，使用前请核对原文。" />
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/projects/:id" element={<ProjectDetailPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/operations" element={<OperationsPage />} />
            <Route path="*" element={<DashboardPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export function App() {
  return <BrowserRouter><Shell /></BrowserRouter>
}

