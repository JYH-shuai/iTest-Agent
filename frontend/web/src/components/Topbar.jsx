import React from 'react';
import { Activity, GitBranch, Cloud, Zap } from 'lucide-react';

export default function Topbar({ health, currentTask }) {
  const online = health?.ok && health?.data?.status === 'ok';
  const version = health?.data?.version || '—';
  return (
    <header className="tb-root">
      <div className="tb-left">
        <h2 className="tb-title">智能测试流水线</h2>
        <span className="tb-breadcrumb">
          <Cloud size={13} />
          &nbsp;Dashboard / Pipeline Runner
        </span>
      </div>

      <div className="tb-right">
        <div className={`tb-pill ${online ? 'online' : 'offline'}`}>
          <span className="tb-dot" />
          {online ? '后端在线' : '后端离线'}
          <span className="tb-pill-sub">v{version}</span>
        </div>

        {currentTask?.task_id && (
          <div className="tb-task">
            <Activity size={14} />
            <span className="tb-task-id">{currentTask.task_id.slice(0, 10)}</span>
            <span className="tb-task-status">
              {{
                pending: '排队中',
                running: '运行中',
                completed: '已完成',
                failed: '失败',
              }[currentTask.status] || currentTask.status}
            </span>
          </div>
        )}

        <div className="tb-meta">
          <GitBranch size={14} />
          <span>main</span>
        </div>
        <div className="tb-meta accent">
          <Zap size={14} />
          <span>MCP Ready</span>
        </div>
      </div>
    </header>
  );
}
