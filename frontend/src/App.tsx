import { useEffect, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Button, ConfigProvider, Drawer, Input, Layout } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { AppstoreOutlined, BellOutlined, CloudSyncOutlined, DatabaseOutlined, MenuOutlined, SettingOutlined, LeftOutlined, RightOutlined, HomeOutlined, ScheduleOutlined, SearchOutlined, RightOutlined as ChevronRight } from '@ant-design/icons'
import { DashboardPage } from './pages/Dashboard'
import { ProjectDetailPage } from './pages/ProjectDetail'
import { SettingsPage } from './pages/SettingsPage'
import { OperationsPage } from './pages/Operations'
import { CollectionPage } from './pages/CollectionPage'

const { Header, Sider, Content } = Layout
const navigation = [
  { to: '/', label: '首页', icon: <HomeOutlined /> },
  { to: '/?view=all', label: '采购需求', icon: <AppstoreOutlined /> },
  { to: '/?view=following', label: '项目跟进', icon: <ScheduleOutlined /> },
  { to: '/operations', label: '处理状态', icon: <CloudSyncOutlined /> },
  { to: '/collection', label: '信息采集', icon: <DatabaseOutlined /> },
  { to: '/settings', label: '系统设置', icon: <SettingOutlined /> },
]

function DossierMark() {
  return (
    <svg viewBox="0 0 40 40" width="32" height="32" fill="none" aria-hidden="true">
      <path d="m20 2 15 8v20l-15 8L5 30V10L20 2Z" fill="#2167ff" />
      <path d="m20 7 10 6v14l-10 6V7Z" fill="#1250d5" />
      <path d="M15 13h8l4 4v10H15V13Z" fill="white" /><path d="M19 18h4v5h-4z" fill="#2167ff" />
    </svg>
  )
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand-block">
      <div className="brand-mark"><DossierMark /></div>
      {!compact && <div className="brand-copy"><strong>采购工作台</strong></div>}
    </div>
  )
}

function Navigation({ compact, onNavigate }: { compact: boolean; onNavigate: () => void }) {
  const location = useLocation()
  const isProject = location.pathname.startsWith('/projects/')
  const selectedView = new URLSearchParams(location.search).get('view')
  const activePath = isProject ? '/?view=all' : location.pathname === '/' && location.search ? selectedView === 'following' ? '/?view=following' : '/?view=all' : location.pathname
  return (
    <nav className="sidebar-nav" aria-label="主导航">
      {navigation.map(item => (
        <Link key={item.to} to={item.to} aria-current={activePath === item.to ? 'page' : undefined} title={compact ? item.label : undefined}
          onClick={onNavigate}
          className={`nav-item${compact ? ' nav-item-compact' : ''}${activePath === item.to ? ' nav-item-active' : ''}`}
        >
          <span className="nav-icon" aria-hidden="true">{item.icon}</span>
          {!compact && <span className="nav-copy"><strong>{item.label}</strong></span>}
        </Link>
      ))}
    </nav>
  )
}

function Shell() {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileMenu, setMobileMenu] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const update = () => setCollapsed(window.innerWidth < 1100)
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  const isProject = location.pathname.startsWith('/projects/')
  const currentView = new URLSearchParams(location.search).get('view')
  const currentTitle = isProject ? '采购详情' : location.pathname === '/settings' ? '系统设置' : currentView === 'following' ? '项目跟进' : currentView === 'ai' ? 'AI 研判' : navigation.find(item => item.to === location.pathname + location.search)?.label ?? '采购需求'
  const isHomeOverview = location.pathname === '/' && !location.search
  const isCatalog = location.pathname === '/' && Boolean(location.search)
  const isFollowing = isCatalog && currentView === 'following'
  const isSettings = location.pathname === '/settings'
  const isCollection = location.pathname === '/collection'
  const isProcurementContent = isProject && new URLSearchParams(location.search).get('tab') === 'procurement'
  const closeMobileMenu = () => setMobileMenu(false)
  const submitHeaderSearch = (value: string) => {
    const params = new URLSearchParams(location.pathname === '/' ? location.search : '')
    if (!params.has('view')) params.set('view', 'all')
    params.set('q', value)
    navigate(`/?${params.toString()}`)
  }
  return <Layout className={`app-layout${isHomeOverview ? ' home-overview-layout' : ''}${isCatalog ? ' catalog-layout' : ''}${isFollowing ? ' following-layout' : ''}${isSettings ? ' settings-layout' : ''}${isCollection ? ' collection-layout' : ''}${isProcurementContent ? ' procurement-content-layout' : ''}`}>
    <Sider className="app-sider desktop-sider" width={isCatalog || isSettings ? 224 : 192} collapsedWidth={72} collapsed={collapsed} trigger={null}>
      <Brand compact={collapsed} /><Navigation compact={collapsed} onNavigate={closeMobileMenu} />
      <div className="sidebar-bottom"><span>{!collapsed && '内部销售工作台'}</span><Button type="text" className="sider-collapse" icon={collapsed ? <RightOutlined /> : <LeftOutlined />} onClick={() => setCollapsed(value => !value)} aria-label={collapsed ? '展开导航' : '收起导航'} /></div>
    </Sider>
    <Drawer placement="left" open={mobileMenu} onClose={closeMobileMenu} className="mobile-drawer" width={260} title={null} closable={false}>
      <Brand /><Navigation compact={false} onNavigate={closeMobileMenu} />
      <Button className="drawer-close" onClick={closeMobileMenu}>关闭导航</Button>
    </Drawer>
    <Layout className={`workspace-layout${location.pathname === '/' ? ' workspace-home' : ''}${isProject ? ' workspace-record' : ''}`}>
      <Header className="app-header">
        <div className="header-location">
          <Button className="shell-menu-button" type="text" icon={<MenuOutlined />} onClick={() => window.innerWidth <= 840 ? setMobileMenu(true) : setCollapsed(value => !value)} aria-label="切换导航" />
          {isHomeOverview ? <Input className="home-header-search" prefix={<SearchOutlined />} placeholder="搜索项目、采购方、设备 / 服务、关键词…" aria-label="搜索项目" onPressEnter={event => submitHeaderSearch(event.currentTarget.value)} /> : isFollowing ? <><span className="followup-breadcrumb-root">采购工作台</span><ChevronRight className="header-chevron" /><strong>{currentTitle}</strong></> : isCatalog ? <><strong>{currentTitle}</strong><ChevronRight className="header-chevron" /></> : <><span>采购工作台</span><ChevronRight className="header-chevron" />{isProject && <><NavLink to="/?view=all">采购需求</NavLink><ChevronRight className="header-chevron" /></>}<strong>{currentTitle}</strong></>}
        </div>
        <div className="header-context">
          {isHomeOverview ? <Link className="home-header-notification" to="/operations" aria-label="查看处理状态"><BellOutlined /></Link> : <><Input.Search className="header-search" prefix={<SearchOutlined />} placeholder="搜索项目、采购方、关键词…" aria-label="搜索项目" onSearch={submitHeaderSearch} enterButton={false} />{isCatalog && <Link className="catalog-header-bell" to="/operations" aria-label="查看处理状态"><BellOutlined /></Link>}</>}
          <span className="workspace-avatar" aria-hidden="true">采</span><span className="workspace-name">采购工作台</span>
        </div>
      </Header>
      <Content className="app-content">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/operations" element={<OperationsPage />} />
          <Route path="/collection" element={<CollectionPage />} />
          <Route path="*" element={<DashboardPage />} />
        </Routes>
      </Content>
    </Layout>
  </Layout>
}

export function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#2167FF', colorInfo: '#2167FF', colorInfoBg: '#EDF2FA', colorInfoBorder: '#CCD8EB',
        colorSuccess: '#397453', colorSuccessBg: '#EDF6F0', colorSuccessBorder: '#C8DECE',
        colorWarning: '#936215', colorWarningBg: '#FFF9EF', colorWarningBorder: '#EBD9B9', colorError: '#B0483E',
        colorText: '#102747', colorTextSecondary: '#64748B', colorBgLayout: '#F5F8FC',
        colorBorder: '#CCD5E0', colorBorderSecondary: '#E2E8F0', borderRadius: 7,
        fontFamily: '"Microsoft YaHei", 微软雅黑, DengXian, sans-serif', fontSize: 14, controlHeight: 36,
      },
      components: {
        Card: { headerFontSize: 16, headerHeight: 52, bodyPadding: 22 },
        Table: { headerBg: '#F3F6FA', headerColor: '#62748A', cellPaddingBlock: 16 },
        Button: { primaryShadow: 'none', defaultShadow: 'none' },
        Tag: { defaultBg: '#F3F6FA', defaultColor: '#62748A' },
      },
    }}>
      <BrowserRouter><Shell /></BrowserRouter>
    </ConfigProvider>
  )
}
