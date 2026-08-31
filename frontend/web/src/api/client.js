import axios from 'axios';

const API_BASE =
  import.meta.env.VITE_API_BASE || ''; /* proxy via vite */

const client = axios.create({
  baseURL: API_BASE,
  timeout: 60_000,
});

/* ---------- Health ---------- */
export const checkHealth = async () => {
  try {
    const { data } = await client.get('/health');
    return { ok: true, data };
  } catch (e) {
    return {
      ok: false,
      data: { status: 'offline', error: String(e?.message || e) },
    };
  }
};

/* ---------- Pipeline ---------- */
export const startPipeline = async ({
  file,
  text,
  model,
  execution_mode,
  mock_llm,
  max_review_rounds,
  llm_api_key = '',
  llm_base_url = '',
  target_url = '',
  sync = false,
}) => {
  const formData = new FormData();
  if (file) {
    formData.append('file', file, file.name || 'prd.md');
  } else if (text && text.trim()) {
    const blob = new Blob([text], { type: 'text/markdown' });
    formData.append('file', blob, 'pasted_prd.md');
  } else {
    throw new Error('请上传 PRD 文件或粘贴 PRD 文本');
  }
  formData.append('model', model);
  formData.append('execution_mode', execution_mode);
  formData.append('mock_llm', mock_llm ? 'true' : 'false');
  formData.append('max_review_rounds', String(max_review_rounds));
  formData.append('llm_api_key', llm_api_key || '');
  formData.append('llm_base_url', llm_base_url || '');
  formData.append('target_url', target_url || '');
  formData.append('sync', sync ? 'true' : 'false');

  const { data } = await client.post('/api/v1/pipeline', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: sync ? 10 * 60 * 1000 : 60_000,
  });
  return data;
};

/* ---------- Task ---------- */
export const getTask = async (taskId) => {
  const { data } = await client.get(`/api/v1/tasks/${taskId}`);
  return data;
};

export const listTasks = async (limit = 20) => {
  const { data } = await client.get('/api/v1/tasks', { params: { limit } });
  return data.tasks || [];
};

/* ---------- Incremental ---------- */
export const runIncremental = async (taskId, changedFunctionIds) => {
  const { data } = await client.post(
    `/api/v1/tasks/${taskId}/incremental`,
    { changed_function_ids: changedFunctionIds, change_type: 'incremental' }
  );
  return data;
};

/* ---------- Report ---------- */
export const getReportMd = async (taskId) => {
  const { data } = await client.get(`/api/v1/tasks/${taskId}/report`, {
    params: { format: 'md' },
    responseType: 'text',
  });
  return data;
};

export const getReportFileUrl = (taskId, format = 'md') => {
  const base = API_BASE || 'http://127.0.0.1:8000';
  return `${base}/api/v1/tasks/${taskId}/report?format=${format}`;
};

export default client;
