import React, { useEffect, useMemo, useRef, useState } from 'react';
import Sidebar from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import PRDUploader from './components/PRDUploader.jsx';
import ParametersPanel from './components/ParametersPanel.jsx';
import PipelineProgress from './components/PipelineProgress.jsx';
import ResultsArea from './components/ResultsArea.jsx';
import { Play, RotateCcw, AlertTriangle } from 'lucide-react';
import {
  checkHealth,
  startPipeline,
  getTask,
  listTasks,
  runIncremental,
  getReportMd,
} from './api/client.js';

/* ============================================================
   App composition + state orchestration
   ============================================================ */

const DEFAULT_PARAMS = {
  model: 'deepseek-chat',
  execution_mode: 'simulated',
  mock_llm: true,
  max_review_rounds: 3,
  llm_api_key: '',
  llm_base_url: 'https://api.deepseek.com/v1',
  target_url: '',
};

const PHASES = ['analyzing', 'generating', 'reviewing', 'executing', 'reporting'];
const PHASE_LABELS_ZH = {
  analyzing: '① 需求分析',
  generating: '② 用例生成',
  reviewing: '③ 用例评审',
  executing: '④ 用例执行',
  reporting: '⑤ 报告生成',
  init: '初始化',
  completed: '✅ 完成',
};
const STATUS_LABELS_ZH = {
  pending: '⏳ 排队中',
  running: '🔄 运行中',
  completed: '✅ 完成',
  failed: '❌ 失败',
};

export default function App() {
  /* ============ Health ============ */
  const [health, setHealth] = useState({ ok: false, data: { status: 'unknown' } });
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const h = await checkHealth();
      if (alive) setHealth(h);
    };
    tick();
    const id = setInterval(tick, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  /* ============ Input state ============ */
  const [file, setFile] = useState(null);
  const [text, setText] = useState('');
  const [params, setParams] = useState(DEFAULT_PARAMS);

  /* ============ Pipeline state ============ */
  const [task, setTask] = useState(null); // { task_id, status, phase, ...}
  const [running, setRunning] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  /* ============ Polling ============ */
  const pollTimerRef = useRef(null);
  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  const pollTask = async (taskId) => {
    try {
      const t = await getTask(taskId);
      setTask(t);
      if (t.status === 'completed' || t.status === 'failed') {
        stopPolling();
        setRunning(false);
        await refreshReport(taskId);
        await refreshHistory();
      }
    } catch (e) {
      console.warn('[poll] error', e);
    }
  };

  const startPolling = (taskId) => {
    stopPolling();
    pollTask(taskId);
    pollTimerRef.current = setInterval(() => pollTask(taskId), 2500);
  };

  useEffect(() => () => stopPolling(), []);

  /* ============ Action: submit ============ */
  const handleSubmit = async () => {
    setSubmitError(null);
    // Validate
    if (params.execution_mode === 'mcp' && !String(params.target_url || '').trim()) {
      setSubmitError('已选择 MCP 真实执行，必须填写被测目标地址');
      return;
    }
    try {
      setRunning(true);
      const useMock =
        params.mock_llm || !String(params.llm_api_key || '').trim();
      const result = await startPipeline({
        file,
        text,
        model: params.model,
        execution_mode: params.execution_mode,
        mock_llm: useMock,
        max_review_rounds: params.max_review_rounds,
        llm_api_key: params.llm_api_key,
        llm_base_url: params.llm_base_url,
        target_url: params.target_url,
        sync: false,
      });
      setTask({
        task_id: result.task_id,
        status: 'running',
        phase: 'analyzing',
        prd_filename: file?.name || (text ? 'pasted_prd.md' : ''),
      });
      startPolling(result.task_id);
    } catch (e) {
      setSubmitError(
        e?.response?.data?.detail || e?.message || String(e)
      );
      setRunning(false);
    }
  };

  /* ============ Results rendering ============ */
  const [reportMd, setReportMd] = useState('');
  const refreshReport = async (taskId) => {
    try {
      setReportMd(await getReportMd(taskId));
    } catch {
      setReportMd('');
    }
  };

  const analysisRows = useMemo(
    () => buildAnalysisRows(task?.analysis),
    [task?.analysis]
  );
  const casesRows = useMemo(
    () => buildCasesRows(task?.test_suite),
    [task?.test_suite]
  );
  const functionIds = useMemo(() => analysisRows.map((r) => r[0]).filter(Boolean), [
    analysisRows,
  ]);
  const [selectedFuncIds, setSelectedFuncIds] = useState([]);

  const logText = useMemo(() => {
    const messages = task?.messages || [];
    const times = task?.phase_times || {};
    const lines = [];
    Object.entries(times).forEach(([k, v]) => {
      if (v) lines.push(`${PHASE_LABELS_ZH[k] || k}: ${v}s`);
    });
    return [...lines, ...messages].join('\n') || '（暂无日志）';
  }, [task]);

  /* ============ History ============ */
  const [historyRows, setHistoryRows] = useState([]);
  const refreshHistory = async () => {
    try {
      const list = await listTasks(20);
      setHistoryRows(
        list.map((t) => [
          (t.task_id || '').slice(0, 12),
          STATUS_LABELS_ZH[t.status] || t.status,
          PHASE_LABELS_ZH[t.phase] || t.phase,
          t.prd_filename || '',
          t.incremental ? '是' : '否',
          t.created_at || '',
          t.task_id || '',
        ])
      );
    } catch {
      setHistoryRows([]);
    }
  };
  useEffect(() => {
    refreshHistory();
  }, []);

  const pickHistory = async (taskId) => {
    if (!taskId) return;
    stopPolling();
    setRunning(false);
    const t = await getTask(taskId);
    setTask(t);
    await refreshReport(taskId);
    setSelectedFuncIds([]);
    setResultsTab('analysis');
  };

  /* ============ Incremental ============ */
  const handleIncremental = async () => {
    if (!task?.task_id || !selectedFuncIds.length) return;
    try {
      setRunning(true);
      const result = await runIncremental(task.task_id, selectedFuncIds);
      setTask({
        task_id: result.task_id,
        status: 'running',
        phase: 'analyzing',
        incremental: true,
      });
      startPolling(result.task_id);
      setSelectedFuncIds([]);
    } catch (e) {
      setSubmitError(
        `增量更新失败：${e?.response?.data?.detail || e?.message || e}`
      );
      setRunning(false);
    }
  };

  /* ============ Reset ============ */
  const handleReset = () => {
    stopPolling();
    setTask(null);
    setRunning(false);
    setReportMd('');
    setSelectedFuncIds([]);
    setSubmitError(null);
  };

  /* ============ Sidebar navigation → results tab ============ */
  const [resultsTab, setResultsTab] = useState('analysis');
  const [activeNav, setActiveNav] = useState('pipeline');
  const resultsRef = useRef(null);

  const NAV_TAB_MAP = {
    pipeline: 'analysis',
    upload: 'analysis',
    cases: 'cases',
    reports: 'report',
    history: 'history',
    settings: 'analysis',
  };

  const handleNavigate = (key) => {
    setActiveNav(key);
    const target = NAV_TAB_MAP[key];
    if (target) {
      setResultsTab(target);
      if (key === 'history') refreshHistory();
      resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  /* Card mouse tracking for highlight */
  const trackCardMouse = (e) => {
    const el = e.currentTarget;
    const rect = el.getBoundingClientRect();
    el.style.setProperty('--mx', `${e.clientX - rect.left}px`);
    el.style.setProperty('--my', `${e.clientY - rect.top}px`);
  };

  return (
    <div className="app-shell">
      <Sidebar active={activeNav} onNavigate={handleNavigate} />
      <Topbar health={health} currentTask={task} />

      <main className="main">
        <div className="split-row">
          <div onMouseMove={trackCardMouse}>
            <PRDUploader
              file={file}
              onFileChange={(f) => {
                setFile(f);
                if (f) setText('');
              }}
              text={text}
              onTextChange={(t) => {
                setText(t);
                if (t) setFile(null);
              }}
            />
          </div>
          <div onMouseMove={trackCardMouse}>
            <ParametersPanel
              params={params}
              onChange={setParams}
              disabled={running}
            />
          </div>
        </div>

        {/* Start bar */}
        <div className="start-bar">
          {submitError && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 14px',
                borderRadius: 10,
                border: '1px solid rgba(239,68,68,0.3)',
                background: 'rgba(239,68,68,0.1)',
                color: '#f87171',
                fontSize: 13,
              }}
            >
              <AlertTriangle size={14} />
              {submitError}
            </div>
          )}
          {task?.task_id && (
            <button className="btn btn-ghost" onClick={handleReset}>
              <RotateCcw size={14} /> 新建任务
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={running || (!file && !text?.trim())}
          >
            <Play size={15} fill="currentColor" />
            {running ? '任务运行中…' : '🚀 启动全流程测试'}
          </button>
        </div>

        <div onMouseMove={trackCardMouse}>
          <PipelineProgress
            task={task || { status: '', phase: '' }}
            phaseTimes={task?.phase_times || {}}
            messages={task?.messages || []}
          />
        </div>

        <div onMouseMove={trackCardMouse} ref={resultsRef}>
          <ResultsArea
            task={task}
            analysisRows={analysisRows}
            casesRows={casesRows}
            reviewData={task?.review || {}}
            executionData={task?.execution || {}}
            reportMd={reportMd}
            logText={logText}
            functionIds={functionIds}
            selectedFuncIds={selectedFuncIds}
            onSelectedFuncChange={setSelectedFuncIds}
            onIncremental={handleIncremental}
            historyRows={historyRows}
            onHistoryPick={pickHistory}
            tab={resultsTab}
            onTabChange={setResultsTab}
          />
        </div>

        <footer
          style={{
            textAlign: 'center',
            color: 'var(--text-tertiary)',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
            padding: 'var(--space-4) 0 var(--space-8)',
            opacity: 0.7,
          }}
        >
          iTest-Agent · LangGraph × MCP × RAG · 智能测试多 Agent 协作系统 ·
          Redesign UI v2.0
        </footer>
      </main>
    </div>
  );
}

/* ============================================================
   Helpers: build result rows (mirror backend Gradio logic)
   ============================================================ */

function _loadJson(filePath) {
  // Frontend cannot read backend filesystem paths.
  // Use embedded summary if provided, otherwise empty.
  return filePath ? {} : {};
}

function buildAnalysisRows(analysisField) {
  const taskSummary = analysisField?.summary || analysisField?.data || null;
  if (taskSummary && Array.isArray(taskSummary.rows) && taskSummary.rows.length) {
    return taskSummary.rows;
  }
  // Fallback: parse function_tree from backend response (inline)
  const tree = analysisField?.function_tree || analysisField?.data?.function_tree;
  if (Array.isArray(tree) && tree.length) {
    const rows = [];
    const walk = (node, level = 0) => {
      rows.push([
        node.id || '',
        `${'　'.repeat(level)}${node.name || ''}`,
        node.priority || '',
      ]);
      (node.sub_functions || []).forEach((s) => walk(s, level + 1));
    };
    tree.forEach((f) => walk(f));
    return rows;
  }
  return [['（暂无数据）', '', '']];
}

function buildCasesRows(suiteField) {
  const list = suiteField?.test_cases || suiteField?.data?.test_cases || [];
  if (!list || !list.length) return [['（暂无数据）', '', '', '', '']];
  return list.map((c) => {
    const steps = Array.isArray(c.steps) ? c.steps : [];
    const stepsStr = steps
      .map((s, i) => {
        if (typeof s === 'string') return `${i + 1}. ${s}`;
        return `${s?.step || i + 1}. ${s?.action || ''}`;
      })
      .join('\n');
    let expected = '';
    if (steps.length) {
      const last = steps[steps.length - 1];
      if (typeof last === 'object') expected = last.expected || '';
    }
    return [
      c.case_id || '',
      c.title || '',
      c.type || c.case_type || '',
      stepsStr,
      expected,
    ];
  });
}
