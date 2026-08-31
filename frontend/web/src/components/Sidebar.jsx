import React from 'react';
import {
  Upload,
  Play,
  FlaskConical,
  FileText,
  History,
  Settings2,
  Rocket,
} from 'lucide-react';

const navItems = [
  { key: 'pipeline', label: '启动流水线', icon: Rocket, active: true },
  { key: 'upload', label: 'PRD 上传', icon: Upload },
  { key: 'cases', label: '用例库', icon: FlaskConical },
  { key: 'reports', label: '测试报告', icon: FileText },
  { key: 'history', label: '历史任务', icon: History },
  { key: 'settings', label: '参数配置', icon: Settings2 },
];

export default function Sidebar({ active = 'pipeline', onNavigate }) {
  return (
    <aside className="sb-root">
      <div className="sb-brand">
        <div className="sb-brand-logo">
          <Play size={18} fill="#22d3ee" />
        </div>
        <div className="sb-brand-text">
          <span className="sb-brand-title">iTest-Agent</span>
          <span className="sb-brand-sub">Intelligent Test Platform</span>
        </div>
      </div>

      <nav className="sb-nav">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = active === item.key;
          return (
            <button
              key={item.key}
              className={`sb-nav-item ${isActive ? 'active' : ''}`}
              onClick={() => onNavigate?.(item.key)}
            >
              <Icon size={18} strokeWidth={1.8} />
              <span>{item.label}</span>
              {isActive && <span className="sb-indicator" />}
            </button>
          );
        })}
      </nav>

      <div className="sb-footer">
        <div className="sb-footer-card">
          <div className="sb-footer-title">多 Agent 协作</div>
          <div className="sb-footer-desc">需求分析 · 生成 · 评审 · 执行 · 报告</div>
          <div className="sb-footer-agents">
            <span>LangGraph</span>
            <span>MCP</span>
            <span>RAG</span>
          </div>
        </div>
        <div className="sb-version">v2.0 · Redesign</div>
      </div>
    </aside>
  );
}
