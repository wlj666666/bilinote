import {
  ArrowUpRight,
  LayoutDashboard,
  ListVideo,
  LogOut,
  Settings2,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import logo from '@/assets/icon.svg'
import './admin.css'

export default function AdminLayout() {
  const user = useAuthStore(state => state.user)
  const logout = useAuthStore(state => state.logout)
  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <Link to="/admin" className="admin-brand">
          <img src={logo} alt="" />
          <span>
            BiliNote<small>管理控制台</small>
          </span>
        </Link>
        <div className="admin-nav-label">工作空间</div>
        <nav aria-label="管理后台导航">
          <NavLink to="/admin" end>
            <LayoutDashboard size={19} />
            <span>系统总览</span>
          </NavLink>
          <NavLink to="/admin/tasks">
            <ListVideo size={19} />
            <span>任务监控</span>
          </NavLink>
          <NavLink to="/admin/users">
            <Users size={19} />
            <span>用户管理</span>
          </NavLink>
        </nav>
        <div className="admin-nav-label admin-nav-secondary">配置</div>
        <nav aria-label="系统配置">
          <Link to="/settings/model">
            <Settings2 size={19} />
            <span>模型与系统设置</span>
            <ArrowUpRight size={14} />
          </Link>
        </nav>
        <div className="admin-sidebar-bottom">
          <Link to="/" className="admin-workspace-link">
            返回笔记工作区
            <ArrowUpRight size={16} />
          </Link>
          <div className="admin-profile">
            <span className="admin-avatar">{user?.username.slice(0, 1).toUpperCase()}</span>
            <span>
              <strong>{user?.username}</strong>
              <small>
                <ShieldCheck size={12} />
                管理员
              </small>
            </span>
            <button
              aria-label="退出登录"
              title="退出登录"
              onClick={() => {
                void logout().catch(() => {})
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="admin-main">
        <header className="admin-topbar">
          <span>
            BiliNote <span>/</span> 管理后台
          </span>
          <span className="admin-access-label">
            <ShieldCheck size={14} />
            管理员专属
          </span>
        </header>
        <div className="admin-content">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
