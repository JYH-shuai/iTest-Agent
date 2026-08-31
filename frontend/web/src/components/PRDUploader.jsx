import React, { useCallback, useRef, useState } from 'react';
import { FileUp, FileText, Upload as UploadIcon, X } from 'lucide-react';

export default function PRDUploader({ onFileChange, file, onTextChange, text }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const openDialog = () => inputRef.current?.click();

  const handleFile = (f) => {
    if (!f) return;
    onFileChange?.(f);
  };

  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      const f = e.dataTransfer?.files?.[0];
      if (f) handleFile(f);
    },
    [onFileChange]
  );

  return (
    <section className="card card-lg upload-root">
      <div className="card-head">
        <div className="card-head-main">
          <div className="card-icon brand">
            <FileUp size={18} />
          </div>
          <div>
            <h3 className="card-title">PRD 需求文档</h3>
            <p className="card-sub">
              上传 Markdown PRD 文件，或直接粘贴需求文本。无 API Key 时可开启 Mock
              演示模式。
            </p>
          </div>
        </div>
        <span className="chip chip-soft">.md 支持</span>
      </div>

      <div
        className={`upload-dropzone ${dragging ? 'dragging' : ''} ${
          file ? 'hasfile' : ''
        }`}
        onClick={openDialog}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".md,.markdown,text/markdown"
          style={{ display: 'none' }}
          onChange={(e) => handleFile(e.target.files?.[0])}
        />

        {file ? (
          <div className="upload-file">
            <div className="upload-file-icon success">
              <FileText size={20} />
            </div>
            <div className="upload-file-info">
              <div className="upload-file-name">{file.name}</div>
              <div className="upload-file-meta">
                {(file.size / 1024).toFixed(1)} KB · 已就绪
              </div>
            </div>
            <button
              className="btn btn-ghost btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                onFileChange?.(null);
              }}
            >
              <X size={14} /> 移除
            </button>
          </div>
        ) : (
          <>
            <div className="upload-illustration">
              <div className="upload-ring">
                <UploadIcon size={26} strokeWidth={1.8} />
              </div>
            </div>
            <div className="upload-title">
              拖拽 PRD 到此处，或
              <span className="upload-browse"> 浏览文件 </span>
            </div>
            <div className="upload-hint">
              支持 .md / .markdown · 也可在下方直接粘贴文本
            </div>
          </>
        )}
      </div>

      <div className="paste-area">
        <div className="paste-label">
          <FileText size={14} /> 粘贴 PRD Markdown 文本（二选一）
        </div>
        <textarea
          className="textarea"
          rows={6}
          placeholder={`# 示例 PRD\n\n## 1. 用户登录\n- 支持邮箱/手机号 + 密码\n- 错误密码连续 5 次锁定账号\n- ……`}
          value={text || ''}
          onChange={(e) => onTextChange?.(e.target.value)}
          disabled={!!file}
        />
      </div>
    </section>
  );
}
