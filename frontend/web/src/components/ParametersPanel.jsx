import React from 'react';
import { Sliders, Wand2, Bot, Cpu, ShieldCheck, Target } from 'lucide-react';

export default function ParametersPanel({ params, onChange, disabled }) {
  const update = (k, v) => onChange?.({ ...params, [k]: v });

  return (
    <section className="card card-lg params-root">
      <div className="card-head">
        <div className="card-head-main">
          <div className="card-icon violet">
            <Sliders size={18} />
          </div>
          <div>
            <h3 className="card-title">运行参数</h3>
            <p className="card-sub">
              灵活配置模型、执行模式与评审策略。无 Key 可直接开启 Mock 离线演示。
            </p>
          </div>
        </div>
        <span className="chip chip-violet">执行配置</span>
      </div>

      <div className="params-grid">
        <div className="form-field col-2">
          <label className="form-label">
            <Bot size={14} /> LLM 模型
          </label>
          <input
            className="form-input"
            value={params.model}
            disabled={disabled}
            onChange={(e) => update('model', e.target.value)}
          />
          <p className="form-hint">默认 deepseek-chat，兼容 OpenAI 协议接口</p>
        </div>

        <div className="form-field col-2">
          <label className="form-label">
            <ShieldCheck size={14} /> API Key（可选）
          </label>
          <input
            type="password"
            className="form-input"
            placeholder="sk-..."
            value={params.llm_api_key || ''}
            disabled={disabled}
            onChange={(e) => update('llm_api_key', e.target.value)}
          />
          <p className="form-hint">留空且未配置服务端 Key 时，自动降级为规则解析</p>
        </div>

        <div className="form-field col-2">
          <label className="form-label">
            <Cpu size={14} /> API Base URL
          </label>
          <input
            className="form-input"
            value={params.llm_base_url || ''}
            disabled={disabled}
            onChange={(e) => update('llm_base_url', e.target.value)}
          />
        </div>

        <div className="form-field">
          <label className="form-label">
            <Target size={14} /> 执行模式
          </label>
          <select
            className="form-select"
            value={params.execution_mode}
            disabled={disabled}
            onChange={(e) => update('execution_mode', e.target.value)}
          >
            <option value="simulated">🧪 Simulated · 模拟执行</option>
            <option value="mcp">⚡ MCP · 真实执行</option>
          </select>
        </div>

        <div className="form-field">
          <label className="form-label">
            <Wand2 size={14} /> 评审最大轮数
          </label>
          <div className="form-slider-row">
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={params.max_review_rounds}
              disabled={disabled}
              onChange={(e) => update('max_review_rounds', Number(e.target.value))}
              className="form-range"
            />
            <span className="form-slider-val">{params.max_review_rounds}</span>
          </div>
        </div>

        <div className="form-field" style={{ gridColumn: '1 / -1' }}>
          <label className="form-label">被测目标地址（MCP 模式必填）</label>
          <input
            className="form-input"
            placeholder="http://127.0.0.1:8090 或 https://api.example.com"
            value={params.target_url || ''}
            disabled={disabled || params.execution_mode !== 'mcp'}
            onChange={(e) => update('target_url', e.target.value)}
          />
          {params.execution_mode === 'mcp' && (
            <p className="form-hint warn">
              MCP 真实执行必须填写被测系统地址；UI 用例作为页面入口，API 用例作为 Base URL
            </p>
          )}
        </div>

        <div className="form-field toggles col-2">
          <label className="toggle">
            <input
              type="checkbox"
              checked={!!params.mock_llm}
              disabled={disabled}
              onChange={(e) => update('mock_llm', e.target.checked)}
            />
            <span className="toggle-track">
              <span className="toggle-thumb" />
            </span>
            <span className="toggle-label">
              Mock LLM · 离线演示（无需 API Key）
            </span>
          </label>
        </div>
      </div>
    </section>
  );
}
