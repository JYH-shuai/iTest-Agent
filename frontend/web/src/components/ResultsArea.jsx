import React, { useMemo, useState } from 'react';
import {
  ListTree,
  FlaskConical,
  Shield,
  Zap,
  FileText as FileReport,
  ScrollText,
  PlusCircle,
  Clock,
  ChevronRight,
  CheckCircle2,
  XCircle,
  SkipForward,
  MinusCircle,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getReportFileUrl } from '../api/client.js';

const TABS = [
  { key: 'analysis', label: '功能点', icon: ListTree },
  { key: 'cases', label: '测试用例', icon: FlaskConical },
  { key: 'review', label: '评审结果', icon: Shield },
  { key: 'execution', label: '执行结果', icon: Zap },
  { key: 'report', label: '测试报告', icon: FileReport },
  { key: 'log', label: '运行日志', icon: ScrollText },
  { key: 'incremental', label: '增量更新', icon: PlusCircle },
  { key: 'history', label: '历史任务', icon: Clock },
];

const TYPE_PILL = {
  正常: 'success',
  功能测试: 'success',
  normal: 'success',
  functional: 'success',
  边界: 'warning',
  boundary: 'warning',
  异常: 'danger',
  exception: 'danger',
  接口测试: 'info',
  api: 'info',
  性能测试: 'violet',
  performance: 'violet',
  安全测试: 'danger',
  兼容性测试: 'info',
};

const PRIORITY_PILL = {
  P0: 'danger',
  P1: 'warning',
  P2: 'info',
};

function StatusBadge({ status }) {
  const map = {
    passed: { icon: CheckCircle2, color: 'success', text: '通过' },
    passed_simulated: { icon: CheckCircle2, color: 'success', text: '通过·模拟' },
    failed: { icon: XCircle, color: 'danger', text: '失败' },
    blocked: { icon: MinusCircle, color: 'warning', text: '阻塞' },
    skipped: { icon: SkipForward, color: 'muted', text: '跳过' },
  };
  const info = map[status] || {
    icon: MinusCircle,
    color: 'muted',
    text: status || '—',
  };
  const Icon = info.icon;
  return (
    <span className={`pill pill-${info.color} pill-icon`}>
      <Icon size={13} />
      {info.text}
    </span>
  );
}

export default function ResultsArea({
  task,
  analysisRows,
  casesRows,
  reviewData,
  executionData,
  reportMd,
  logText,
  functionIds,
  selectedFuncIds,
  onSelectedFuncChange,
  onIncremental,
  historyRows,
  onHistoryPick,
  tab: tabProp,
  onTabChange,
}) {
  const [tabInner, setTabInner] = useState('analysis');
  const tab = tabProp ?? tabInner;
  const setTab = (t) => {
    setTabInner(t);
    onTabChange?.(t);
  };
  const hasTask = !!task?.task_id;

  return (
    <section className="card results-root">
      <div className="card-head results-head">
        <div className="card-head-main">
          <div className="card-icon success">
            <FileReport size={18} />
          </div>
          <div>
            <h3 className="card-title">测试结果</h3>
            <p className="card-sub">
              {hasTask
                ? `任务 ${task.task_id.slice(0, 10)} · ${
                    task.prd_filename || '—'
                  }`
                : '提交 PRD 启动全流程，实时查看各阶段产物'}
            </p>
          </div>
        </div>
        {hasTask && (
          <div className="results-actions">
            <a
              className="btn btn-ghost btn-sm"
              href={getReportFileUrl(task.task_id, 'md')}
              target="_blank"
              rel="noreferrer"
            >
              下载 MD
            </a>
            <a
              className="btn btn-ghost btn-sm"
              href={getReportFileUrl(task.task_id, 'pdf')}
              target="_blank"
              rel="noreferrer"
            >
              下载 PDF
            </a>
            <a
              className="btn btn-outline btn-sm"
              href={getReportFileUrl(task.task_id, 'json')}
              target="_blank"
              rel="noreferrer"
            >
              导出 JSON
            </a>
          </div>
        )}
      </div>

      <div className="tabs">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              className={`tab ${tab === t.key ? 'active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="tab-panel fade-in">
        {tab === 'analysis' && (
          <TabAnalysis rows={analysisRows} empty={!hasTask} />
        )}
        {tab === 'cases' && <TabCases rows={casesRows} empty={!hasTask} />}
        {tab === 'review' && <TabReview data={reviewData} empty={!hasTask} />}
        {tab === 'execution' && (
          <TabExecution data={executionData} empty={!hasTask} />
        )}
        {tab === 'report' && <TabReport md={reportMd} empty={!hasTask} />}
        {tab === 'log' && <TabLog text={logText} empty={!hasTask} />}
        {tab === 'incremental' && (
          <TabIncremental
            ids={functionIds}
            selected={selectedFuncIds}
            onSelect={onSelectedFuncChange}
            onRun={onIncremental}
            empty={!hasTask}
          />
        )}
        {tab === 'history' && (
          <TabHistory rows={historyRows} onPick={onHistoryPick} />
        )}
      </div>
    </section>
  );
}

/* ============================================================
   Sub-panels
   ============================================================ */

function EmptyState({ title, hint, Icon }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Icon size={34} />
      </div>
      <div className="empty-title">{title}</div>
      <div className="empty-hint">{hint}</div>
    </div>
  );
}

function TabAnalysis({ rows, empty }) {
  if (empty || !rows?.length)
    return (
      <EmptyState
        title="暂无功能点"
        hint="提交 PRD 后，需求分析 Agent 将提取功能树、优先级和验收标准。"
        Icon={ListTree}
      />
    );
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: 180 }}>功能 ID</th>
            <th>功能名称</th>
            <th style={{ width: 100 }}>优先级</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="mono muted">{r[0]}</td>
              <td>{r[1]}</td>
              <td>
                <span
                  className={`pill pill-${
                    PRIORITY_PILL[String(r[2])] || 'muted'
                  }`}
                >
                  {r[2]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TabCases({ rows, empty }) {
  if (empty || !rows?.length)
    return (
      <EmptyState
        title="暂无用例"
        hint="用例生成 Agent 将基于功能点和知识库，输出结构化测试用例。"
        Icon={FlaskConical}
      />
    );
  return (
    <div className="table-wrap">
      <table className="data-table dense">
        <thead>
          <tr>
            <th style={{ width: 110 }}>用例 ID</th>
            <th style={{ width: 200 }}>标题</th>
            <th style={{ width: 100 }}>类型</th>
            <th>步骤</th>
            <th style={{ width: 220 }}>预期结果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="mono muted">{r[0]}</td>
              <td className="strong">{r[1]}</td>
              <td>
                <span
                  className={`pill pill-${
                    TYPE_PILL[String(r[2] || '').toLowerCase()] || 'info'
                  }`}
                >
                  {r[2] || '—'}
                </span>
              </td>
              <td className="mono small pre-wrap">{r[3]}</td>
              <td className="pre-wrap">{r[4]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TabReview({ data, empty }) {
  if (empty || !data)
    return (
      <EmptyState
        title="暂无评审结果"
        hint="评审 Agent 校验覆盖率、步骤完整性和边界/异常占比。"
        Icon={Shield}
      />
    );
  const score = data?.score ?? 0;
  const passed = !!data?.passed || !!data?.approved;
  const gapPct = useMemo(() => Math.min(100, Math.max(0, Number(score))), [
    score,
  ]);
  return (
    <div className="grid-2">
      <div className="review-score-card">
        <div className="review-ring-wrap">
          <svg viewBox="0 0 120 120" className="review-ring">
            <circle
              cx="60"
              cy="60"
              r="52"
              stroke="rgba(148,163,184,0.12)"
              strokeWidth="10"
              fill="none"
            />
            <circle
              cx="60"
              cy="60"
              r="52"
              stroke="url(#reviewGrad)"
              strokeWidth="10"
              fill="none"
              strokeLinecap="round"
              strokeDasharray={`${(gapPct / 100) * 2 * Math.PI * 52} ${
                2 * Math.PI * 52
              }`}
              transform="rotate(-90 60 60)"
              style={{ transition: 'stroke-dasharray 600ms ease-out' }}
            />
            <defs>
              <linearGradient id="reviewGrad" x1="0" x2="1" y1="0" y2="1">
                <stop offset="0%" stopColor="#22d3ee" />
                <stop offset="100%" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
          </svg>
          <div className="review-score-center">
            <div className="review-score-num">{score}</div>
            <div className="review-score-sub">/ 100 分</div>
          </div>
        </div>
        <div className={`review-verdict ${passed ? 'pass' : 'fail'}`}>
          {passed ? '✅ 评审通过' : '❌ 评审不通过'}
        </div>
      </div>
      <div className="kv-list">
        {Object.entries(data || {}).map(([k, v]) => {
          if (v == null || v === '' || Array.isArray(v) && !v.length)
            return null;
          if (['score', 'passed', 'approved'].includes(k)) return null;
          const display = Array.isArray(v) ? v.join('，') : v;
          return (
            <div key={k} className="kv-item">
              <div className="kv-k">{k}</div>
              <div className="kv-v">{String(display)}</div>
            </div>
          );
        })}
        {Object.keys(data || {}).length <= 2 && (
          <div className="muted small">暂无更多评审细节</div>
        )}
      </div>
    </div>
  );
}

function TabExecution({ data, empty }) {
  if (empty || !data)
    return (
      <EmptyState
        title="暂无执行结果"
        hint="执行 Agent 通过 MCP Server 调用真实工具或模拟执行。"
        Icon={Zap}
      />
    );
  const {
    total = 0,
    passed = 0,
    failed = 0,
    blocked = 0,
    skipped = 0,
    pass_rate,
    duration_seconds,
    execution_mode,
  } = data || {};
  const stats = [
    { label: '总用例', value: total, color: 'info', icon: FlaskConical },
    { label: '通过', value: passed, color: 'success', icon: CheckCircle2 },
    { label: '失败', value: failed, color: 'danger', icon: XCircle },
    { label: '跳过', value: skipped, color: 'muted', icon: SkipForward },
  ];
  const modeIsSimulated = execution_mode === 'simulated';
  return (
    <div>
      <div
        className={`exec-banner ${
          modeIsSimulated ? 'simulated' : 'mcp'
        }`}
      >
        {modeIsSimulated ? (
          <>
            <Zap size={18} />
            <div>
              <strong>模拟执行 (Simulated)</strong>
              <p>
                未连接真实被测系统，结果由执行引擎规则推演；仅用于演示全流程闭环。
              </p>
            </div>
          </>
        ) : (
          <>
            <Zap size={18} fill="currentColor" />
            <div>
              <strong>MCP 真实执行</strong>
              <p>通过 MCP Server 调用 Playwright / API Test 真实工具。</p>
            </div>
          </>
        )}
      </div>
      <div className="stat-grid">
        {stats.map((s) => {
          const Icon = s.icon;
          return (
            <div key={s.label} className={`stat stat-${s.color}`}>
              <div className="stat-icon">
                <Icon size={18} />
              </div>
              <div className="stat-label">{s.label}</div>
              <div className="stat-value">{s.value}</div>
            </div>
          );
        })}
        <div className="stat stat-violet">
          <div className="stat-icon">
            <CheckCircle2 size={18} />
          </div>
          <div className="stat-label">通过率</div>
          <div className="stat-value">
            {pass_rate != null
              ? `${(Number(pass_rate) * 100).toFixed(0)}%`
              : total
              ? `${Math.round((passed / total) * 100)}%`
              : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="stat-icon">
            <Clock size={18} />
          </div>
          <div className="stat-label">耗时</div>
          <div className="stat-value">
            {duration_seconds != null
              ? `${Number(duration_seconds).toFixed(1)}s`
              : '—'}
          </div>
        </div>
      </div>
      {blocked ? (
        <StatusBadge status="blocked" />
      ) : null}
    </div>
  );
}

function TabReport({ md, empty }) {
  if (empty || !md)
    return (
      <EmptyState
        title="暂无报告"
        hint="报告 Agent 将输出需求概览、用例统计、执行结果与缺陷聚类。"
        Icon={FileReport}
      />
    );
  return (
    <div className="md-render">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{md || ''}</ReactMarkdown>
    </div>
  );
}

function TabLog({ text, empty }) {
  if (empty || !text)
    return (
      <EmptyState
        title="暂无日志"
        hint="全流程执行消息和节点日志将实时输出。"
        Icon={ScrollText}
      />
    );
  return (
    <pre className="log-box mono small">
      <code>{text}</code>
    </pre>
  );
}

function TabIncremental({ ids, selected, onSelect, onRun, empty }) {
  if (empty)
    return (
      <EmptyState
        title="增量更新暂不可用"
        hint="完成一次全流程后，可在此勾选变更功能点执行增量用例。"
        Icon={PlusCircle}
      />
    );
  const toggle = (id) => {
    const set = new Set(selected || []);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onSelect?.(Array.from(set));
  };
  return (
    <div>
      <p className="muted small mb-16">
        需求变更后仅需要重新生成变更功能对应用例，其他功能继承历史结果。
      </p>
      <div className="func-checks">
        {(ids || []).map((id) => (
          <label key={id} className="func-check">
            <input
              type="checkbox"
              checked={(selected || []).includes(id)}
              onChange={() => toggle(id)}
            />
            <span className="func-box" />
            <span className="mono">{id}</span>
          </label>
        ))}
        {!ids?.length && (
          <div className="muted small">暂无可选功能 ID</div>
        )}
      </div>
      <div style={{ marginTop: 20 }}>
        <button
          className="btn btn-primary"
          disabled={!selected?.length}
          onClick={onRun}
        >
          <PlusCircle size={16} /> 触发增量更新
        </button>
      </div>
    </div>
  );
}

function TabHistory({ rows, onPick }) {
  if (!rows?.length)
    return (
      <EmptyState
        title="暂无历史任务"
        hint="最近 20 条任务将显示在此处，点击行可加载结果回看。"
        Icon={Clock}
      />
    );
  return (
    <div className="table-wrap">
      <table className="data-table clickable">
        <thead>
          <tr>
            <th style={{ width: 140 }}>任务 ID</th>
            <th>PRD 文件</th>
            <th style={{ width: 120 }}>阶段</th>
            <th style={{ width: 120 }}>状态</th>
            <th style={{ width: 80 }}>增量</th>
            <th style={{ width: 180 }}>创建时间</th>
            <th style={{ width: 60 }}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} onClick={() => onPick?.(r[6] || r[0])}>
              <td className="mono muted">{r[0]}</td>
              <td>{r[3]}</td>
              <td>{r[2]}</td>
              <td>{r[1]}</td>
              <td>{r[4]}</td>
              <td className="muted small">{r[5]}</td>
              <td>
                <ChevronRight size={16} className="muted" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
