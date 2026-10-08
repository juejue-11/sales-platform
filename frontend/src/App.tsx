import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Button, ConfigProvider, Drawer, Layout } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { AppstoreOutlined, CloudSyncOutlined, MenuOutlined, SettingOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons'
import { DashboardPage } from './pages/Dashboard'
import { ProjectDetailPage } from './pages/ProjectDetail'
import { SettingsPage } from './pages/SettingsPage'
import { OperationsPage } from './pages/Operations'

const { Header, Sider, Content } = Layout
const navigation = [
  { to: '/', label: '机会池', description: '看需求，选方案', icon: <AppstoreOutlined />, end: true },
  { to: '/operations', label: '处理状态', description: '同步与分析进度', icon: <CloudSyncOutlined />, end: false },
  { to: '/settings', label: '业务配置', description: '能力、规则与模型', icon: <SettingOutlined />, end: false },
]

function DossierMark() {
  return <svg viewBox="0 0 40 40" width="38" height="38" fill="none" aria-hidden="true"><path d="M9 7h15l7 7v19H9V7Z" stroke="currentColor" strokeWidth="1.8" /><path d="M24 7v8h7M15 19h10M15 24h5" stroke="currentColor" strokeWidth="1.8" /><path d="m21 29 3 3 7-7" stroke="#A9D5CF" strokeWidth="2.3" /></svg>
}

function Brand({ compact = false }: { compact?: boolean }) {
  return <div className="brand-block"><div className="brand-mark"><DossierMark /></div>{!compact && <div className="brand-copy"><strong>商机判断</strong><span>销售研判工作台</span></div>}</div>
}

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
  const isProject = location.pathname.startsWith('/projects/')
  const currentTitle = isProject ? '项目详情' : navigation.find(item => item.to === location.pathname)?.label ?? '机会池'
  const today = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
  const menu = (compact: boolean) => <nav className="sidebar-nav" aria-label="主导航">{navigation.map(item => <NavLink
    key={item.to} to={item.to} end={item.end} title={compact ? item.label : undefined} onClick={() => setMobileMenu(false)}
    className={({ isActive }) => `nav-item${compact ? ' nav-item-compact' : ''}${isActive || (item.to === '/' && isProject) ? ' nav-item-active' : ''}`}
  ><span className="nav-icon">{item.icon}</span>{!compact && <span className="nav-copy"><strong>{item.label}</strong><span>{item.description}</span></span>}</NavLink>)}</nav>
  return <Layout className="app-layout">
    <Sider className="app-sider desktop-sider" width={212} collapsedWidth={72} collapsed={collapsed} trigger={null}>
      <Brand compact={collapsed} />{menu(collapsed)}
      {!collapsed && <div className="sidebar-process"><p>从信息到商业决定</p><ol><li>接入采购资料</li><li>筛选合适机会</li><li>理解 AI 方案</li><li>确认推进动作</li></ol><span>方案由人确认</span></div>}
      <div className="sidebar-bottom"><span>{!collapsed && '内部销售工作台'}</span><Button type="text" className="sider-collapse" icon={collapsed ? <RightOutlined /> : <LeftOutlined />} onClick={() => setCollapsed(value => !value)} aria-label={collapsed ? '展开导航' : '收起导航'} /></div>
    </Sider>
    <Drawer placement="left" open={mobileMenu} onClose={() => setMobileMenu(false)} className="mobile-drawer" width={260} title={null} closable={false}><Brand />{menu(false)}<Button className="drawer-close" onClick={() => setMobileMenu(false)}>关闭导航</Button></Drawer>
    <Layout className="workspace-layout">
      <Header className="app-header">
        <div className="header-location"><Button className="mobile-menu-button" type="text" icon={<MenuOutlined />} onClick={() => setMobileMenu(true)} aria-label="打开导航" /><span>销售研判</span><span className="header-slash">/</span><strong>{currentTitle}</strong></div>
        <div className="header-context"><time>{today}</time><span className="internal-badge">内部使用</span></div>
      </Header>
      <Content className="app-content"><Routes><Route path="/" element={<DashboardPage />} /><Route path="/projects/:id" element={<ProjectDetailPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="/operations" element={<OperationsPage />} /><Route path="*" element={<DashboardPage />} /></Routes></Content>
    </Layout>
  </Layout>
}

export function App() {
  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#216C67', colorInfo: '#216C67', colorInfoBg: '#EDF6F5', colorInfoBorder: '#C8DEDA', colorSuccess: '#2C7965', colorSuccessBg: '#EDF6F0', colorSuccessBorder: '#C8DECE', colorWarning: '#AD6D17', colorWarningBg: '#FFF9EF', colorWarningBorder: '#EBD9B9', colorError: '#B0483E', colorText: '#213F46', colorTextSecondary: '#667980', colorBgLayout: '#EEF3F4', colorBorder: '#CAD8DA', colorBorderSecondary: '#DFE7E8', borderRadius: 5, fontFamily: 'DengXian, 等线, Microsoft YaHei, 微软雅黑, sans-serif', fontSize: 14, controlHeight: 36 }, components: { Card: { headerFontSize: 16, headerHeight: 52, bodyPadding: 22 }, Table: { headerBg: '#F0F5F5', headerColor: '#526D73', cellPaddingBlock: 16 }, Button: { primaryShadow: 'none', defaultShadow: 'none' }, Tag: { defaultBg: '#F2F5F5', defaultColor: '#60757B' } } }}><BrowserRouter><Shell /></BrowserRouter></ConfigProvider>
}
