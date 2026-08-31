import React from 'react';
import {
  Search,
  Sparkles,
  ShieldCheck,
  Zap,
  FileBarChart,
  CheckCircle2,
  Circle,
  Loader2,
} from 'lucide-react';

const PHASES = [
  {
    key: 'analyzing',
    label: '需求分析',
    sub: '功能点提取 · 优先级',
    icon: Search,
  },
  {
    key: 'generating',
    label: '用例生成',
    sub: '结构化用例 · RAG 检索',
    icon: Sparkles,
  },
  {
    key: 'reviewing',
    label: '用例评审',
    sub: '覆盖率门槛 · 回退收敛',
    icon: ShieldCheck,
  },
  {
    key: 'executing',
    label: '用例执行',
    sub: 'MCP/模拟 · 重试机制',
    icon: Zap,
  },
  {
    key: 'reporting',
    label: '报告生成',
    sub: 'Markdown / PDF / JSON',
    icon: FileBarChart,
  },
];

const STATUS_LABELS = {
  pending: { text: '排队中', cls: 'pending' },
  running: { text: '运行中', cls: 'running' },
  completed: { text: '全部完成', cls: 'completed' },
  failed: { text: '任务失败', cls: 'failed' },
};

export default function PipelineProgress({
  task,
  phaseTimes = {},
  messages = [],
}) {
  const phase = task?.phase || '';
  const status = task?.status || '';
  const statusInfo = STATUS_LABELS[status] || { text: status, cls: '' };

  const currentIdx = PHASES.findIndex((p) => p.key === phase);
  const allDone = status === 'completed';

  return (
    <section className="card progress-root">
      <div className="progress-head">
        <div className="progress-head-left">
          <h3 className="card-title inline-t">流水线进度</h3>
          <span className={`progress-status status-${statusInfo.cls}`}>
            {status === 'running' && (
              <Loader2 size={13} className="spin" />
            )}
            {statusInfo.text}
          </span>
        </div>
        <div className="progress-times">
          {Object.entries(phaseTimes).slice(0, 5).map(([k, v]) => {
            const phaseInfo = PHASES.find((p) => p.key === k);
            if (!phaseInfo || !v) return null;
            return (
              <span key={k} className="progress-time-chip">
                {phaseInfo.label.split('').slice(0, 2).join('')}
                <strong>{typeof v === 'number' ? v.toFixed(1) : v}s</strong>
              </span>
            );
          })}
        </div>
      </div>

      <div className="progress-track">
        {PHASES.map((p, i) => {
          const Icon = p.icon;
          const done = allDone || i < currentIdx;
          const active = !allDone && i === currentIdx && status === 'running';
          const isFailed = status === 'failed' && i === currentIdx;
          return (
            <React.Fragment key={p.key}>
              <div
                className={`progress-node ${done ? 'done' : ''} ${
                  active ? 'active' : ''
                } ${isFailed ? 'failed' : ''}`}
              >
                <div className="progress-node-icon">
                  {done ? (
                    <CheckCircle2 size={20} />
                  ) : active ? (
                    <Loader2 size={20} className="spin" />
                  ) : (
                    <Circle size={20} strokeWidth={1.6} />
                  )}
                </div>
                <div className="progress-node-body">
                  <div className={`progress-node-label ${active ? 'accent' : ''}`}>
                    <Icon size={14} />
                    {p.label}
                  </div>
                  <div className="progress-node-sub">{p.sub}</div>
                </div>
              </div>
              {i < PHASES.length - 1 && (
                <div
                  className={`progress-connector ${
                    done ? 'done' : active ? 'partial' : ''
                  }`}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {messages?.length > 0 && (
        <div className="progress-log">
          {messages.slice(-4).map((m, i) => (
            <div
              key={i}
              className="progress-log-line"
              style={{
                animation: `fadeIn 240ms ${i * 60}ms both ease-out`,
              }}
            >
              <span className="progress-log-dot" />
              <span className="mono">{m}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
